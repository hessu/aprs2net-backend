#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import traceback

import requests
import json

# All configuration variables need to be strings originally.
CONFIG_SECTION = 'dns'
DEFAULT_CONF = {
    # Server polling interval
    'poll_interval': '60',
    
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
        
        self.dns_master = self.config.get(CONFIG_SECTION, 'dns_master')
        self.poll_interval = self.config.getint(CONFIG_SECTION, 'poll_interval')
        self.domains = self.config.get(CONFIG_SECTION, 'domains').split(',')
        self.pollers = self.config.get(CONFIG_SECTION, 'pollers').split(' ')
        
        self.rhead = {'User-agent': 'aprs2net-dns/2.0'}
        self.http_timeout = 10.0
    
    def fetch_full_status(self):
        """
        Fetch full status from each of the masters
        """
        for url in self.pollers:
            self.log.info("Fetching status: %s", url)
            
            t_start = time.time()
            
            try:
                r = requests.get('%sapi/full' % url, headers=self.rhead, timeout=self.http_timeout)
                d = r.content
            except Exception as e:
                self.log.error("%s: HTTP full JSON status fetch: Connection error: %s", url, str(e))
            
            if r.status_code != 200:
                self.log.error("%s: HTTP full JSON status fetch: Status code %d", url, r.status_code)
                continue
            
            t_end = time.time()
            t_dur = t_end - t_start
            
            self.log.debug("%s: HTTP GET /api/full returned: %r", url, r.status_code)
            
            try:
                j = json.loads(d)
            except Exception as e:
                self.log.error("%s: JSON parsing failed: %r", url, e)
    
    def poll(self):
        """
        Do a single polling round
        """
        
        # Fetch full status JSON from all pollers, ignoring
        # pollers which appear to be faulty
        self.fetch_full_status()
        # Merge status JSONs, ignoring old polling results for individual servers,
        # figure out per-server "final score"
        #self.merge_status()
        # Push current DNS status to the master, if it has changed
        #self.update_dns()
    
    def loop(self):
        """
        Main DNS driver loop
        """
        
        while True:
            self.poll()
            time.sleep(self.poll_interval)
        
driver = DNSDriver()
driver.loop()

