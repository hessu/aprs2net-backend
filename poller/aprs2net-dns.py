#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import traceback
import types
from urlparse import urlparse

import requests
import json

import aprs2_redis
import aprs2_config

# All configuration variables need to be strings originally.
CONFIG_SECTION = 'dns'
DEFAULT_CONF = {
    # Server polling interval
    'poll_interval': '60',
    
    # Max test result age
    'max_test_result_age': '300',
    
    # Portal URL for downloading configs
    'portal_base_url': 'https://home.tomh.us:8001'
}

class DNSDriver:
    """
    aprs2.net DNS driver
    """
    def __init__(self, config_file='poller.conf'):
        # read logging config file
        logging.config.fileConfig(config_file)
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('main')
        self.log.info("Starting up")
        self.log_poller = logging.getLogger('dns')
        
        # read configuration
        self.config = ConfigParser.ConfigParser()
        self.config.add_section(CONFIG_SECTION)
        
        for option, value in DEFAULT_CONF.iteritems():
            self.config.set(CONFIG_SECTION, option, value)
            
        self.config.read(config_file)
        
        self.portal_base_url = self.config.get(CONFIG_SECTION, 'portal_base_url')
        self.dns_master = self.config.get(CONFIG_SECTION, 'dns_master')
        self.poll_interval = self.config.getint(CONFIG_SECTION, 'poll_interval')
        self.domains = self.config.get(CONFIG_SECTION, 'domains').split(',')
        self.pollers = self.config.get(CONFIG_SECTION, 'pollers').split(' ')
        self.max_test_result_age = self.config.getint(CONFIG_SECTION, 'max_test_result_age')
        
        self.rhead = {'User-agent': 'aprs2net-dns/2.0'}
        self.http_timeout = 10.0
        
        # redis client
        self.red = aprs2_redis.APRS2Redis(db=1)
        self.config_manager = aprs2_config.ConfigManager(logging.getLogger('config'), self.red, self.portal_base_url)
    
    def fetch_full_status(self):
        """
        Fetch full status from each of the masters
        """
        
        status_set = {}
        
        for url in self.pollers:
            self.log.info("Fetching status: %s", url)
            siteid = urlparse(url).netloc
            
            t_start = time.time()
            
            try:
                r = requests.get('%sapi/full' % url, headers=self.rhead, timeout=self.http_timeout)
                d = r.content
            except Exception as e:
                self.log.error("%s: HTTP full JSON status fetch: Connection error: %s", siteid, str(e))
                continue
            
            if r.status_code != 200:
                self.log.error("%s: HTTP full JSON status fetch: Status code %d", siteid, r.status_code)
                continue
            
            t_end = time.time()
            t_dur = t_end - t_start
            
            self.log.debug("%s: HTTP GET /api/full returned: %r (%.3f s)", siteid, r.status_code, t_dur)
            
            try:
                j = json.loads(d)
            except Exception as e:
                self.log.error("%s: JSON parsing failed: %r", url, e)
                continue
            
            self.check_returned_status(siteid, j, status_set)
        
        return status_set
    
    def check_returned_status(self, siteid, j, status_set):
        """
        Check if the returned full status set is any good
        """
        
        if j.get('result') != 'ok':
            self.log.error("%s: Full status JSON does not have result: ok", siteid)
            return
        
        servers = j.get('servers')
        if not servers:
            self.log.error("%s: Full status JSON does not contain servers", siteid)
            return
        
        if not type(servers) is list:
            self.log.error("%s: Full status JSON: servers is not a list", siteid)
            return
        
        # TODO: Check that a good amount of servers in the set are OK,
        # discard the whole set if the poller itself is in trouble
        
        for s in servers:
            self.add_returned_server(siteid, s, status_set)
    
    def add_returned_server(self, siteid, s, status_set):
        """
        Add a single returned server to current status set
        """
        
        cfg = s.get('config')
        stat = s.get('status')
        
        if cfg == None or stat == None:
            self.log.error("%s: Server in set, with config or status missing", siteid)
            return
        
        id = cfg.get('id')
        last_test = stat.get('last_test')
        
        if id == None or last_test == None:
            self.log.error("%s: Server in set, with id or last_test missing", siteid)
            return
        
        test_age = time.time() - last_test
        if test_age > self.max_test_result_age:
            self.log.error("%s: [%s] test age %.0f > %.0f", siteid, id, test_age, self.max_test_result_age)
            return
        
        if id in status_set:
            set = status_set[id]
        else:
            set = status_set[id] = {}
        
        set[siteid] = stat
    
    def merge_status(self, status_set):
        """
        Merge status sets to produce final score for each server
        """
        
        merged = {}
        
        for id in status_set:
            ok_count = 0
            scores = []
            score_sum = 0
            
            for site in status_set[id]:
                stat = status_set[id][site]
                
                #self.log.debug("status for %s at %s: %r", id, site, stat)
                
                status = stat.get('status', 'Unknown')
                if status == 'ok':
                    ok_count += 1
                
                props = stat.get('props', {})
                
                if 'score' in props:
                    scores.append(props['score'])
                    score_sum += props['score']
            
            if ok_count == len(status_set[id]):
                status = 'ok'
            else:
                status = 'fail'
            
            merged[id] = m = {
                'status': status,
                'c_ok': ok_count,
                'c_res': len(status_set[id])
            }
            
            # start off with arithmetic mean of scores
            if len(scores) > 0:
               m['score'] = score_sum / len(scores)
            
            self.log.debug("merged status for %s: %r", id, merged[id])
        
        return merged
    
    def update_dns(self, merged_status):
        """
        Update DNS to match the current merged status
        """
        
        rotates = self.red.getRotates()
        #self.log.debug("rotates: %r", rotates)
        
        for d in rotates:
            self.update_dns_rotate(d, rotates[d])
    
    def update_dns_rotate(self, domain, config):
        """
        Update a single DNS rotate
        """
        self.log.debug("Checking rotate %s: %r", domain, config)
    
    def poll(self):
        """
        Do a single polling round
        """
        
        # Fetch full status JSON from all pollers, ignoring
        # pollers which appear to be faulty
        status_set = self.fetch_full_status()
        # Merge status JSONs, ignoring old polling results for individual servers,
        # figure out per-server "final score"
        merged_status = self.merge_status(status_set)
        # Push current DNS status to the master, if it has changed
        self.update_dns(merged_status)
    
    def loop(self):
        """
        Main DNS driver loop
        """
        
        while True:
            self.poll()
            time.sleep(self.poll_interval)
        
driver = DNSDriver()
driver.loop()

