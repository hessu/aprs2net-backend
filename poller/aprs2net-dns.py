#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import traceback
import types
import socket
from urlparse import urlparse

import requests
import json

import aprs2_redis
import aprs2_config

# dnspython.org
import dns.query
import dns.tsigkeyring
import dns.update

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
        
        self.log = logging.getLogger('dns')
        self.log.info("Starting up")
        
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
        self.master_rotate = self.config.get(CONFIG_SECTION, 'master_rotate')
        self.pollers = self.config.get(CONFIG_SECTION, 'pollers').split(' ')
        self.max_test_result_age = self.config.getint(CONFIG_SECTION, 'max_test_result_age')
        
        self.dns_keyring = dns.tsigkeyring.from_text({ 'aprs2net-dns.' : self.config.get(CONFIG_SECTION, 'dns_tsig_key') })

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
        
        # the testing result must be quite recent to be useful
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
            
            # start off with arithmetic mean of scores... later, figure out
            # something more sensible
            if len(scores) > 0:
               m['score'] = score_sum / len(scores)
            
            self.log.debug("merged status for %s: %r", id, merged[id])
        
        return merged
    
    def update_dns(self, merged_status):
        """
        Update DNS to match the current merged status
        """
        
        # Fetch setup from database (maintaned by config manager thread)
        rotates = self.red.getRotates()
        servers = self.red.getServers()
        
        for d in rotates:
            self.update_dns_rotate(d, rotates[d], merged_status, servers)
    
    def update_dns_rotate(self, domain, domain_conf, status, servers):
        """
        Update a single DNS rotate
        """
        self.log.info("Processing rotate %s ...", domain)
        #self.log.debug("Checking rotate %s: %r", domain, domain_conf)
        
        # which members are OK, and which of them have IPv4 or IPv6 addresses available
        members = domain_conf.get('members')
        members_ok = [i for i in members if status.get(i) and status.get(i).get('status') == 'ok' and status.get(i).get('score') != None and servers.get(i)]
        members_ok_v4 = [i for i in members_ok if servers.get(i).get('ipv4')]
        members_ok_v6 = [i for i in members_ok if servers.get(i).get('ipv6')]
        
        self.log.debug("Members: %r", members)
        self.log.debug("Members ok ip4: %r", members_ok_v4)
        self.log.debug("Members ok ip6: %r", members_ok_v6)
        
        # sort by score
        scored_order_v4 = sorted(members_ok_v4, key=lambda x:status.get(x).get('score'))
        scored_order_v6 = sorted(members_ok_v6, key=lambda x:status.get(x).get('score'))
        
        # Limit the sizes of rotates.
        # The DNS reply packet needs to be <= 512 bytes, since there are still
        # broken resolvers out there, which don't do EDNS or TCP.
        scored_order_v4 = scored_order_v4[0:8]
        scored_order_v6 = scored_order_v6[0:3]
        
        self.log.info("Scored order ip4: %r", [(i, '%.1f' % status.get(i).get('score')) for i in scored_order_v4])
        self.log.info("Scored order ip6: %r", [(i, '%.1f' % status.get(i).get('score')) for i in scored_order_v6])
        
        if len(scored_order_v4) < 1:
            if domain == self.master_rotate:
                self.log.error("Ouch! Master rotate %s has no working servers - not doing anything!", self.master_rotate)
                return
            
            self.log.info("VERDICT %s: No working servers, CNAME %s", domain, self.master_rotate)
            #self.dns_push(domain, 'CNAME', self.master_rotate)
            return
        
        # Addresses to use
        v4_addrs = [servers.get(i).get('ipv4') for i in scored_order_v4]
        v6_addrs = [servers.get(i).get('ipv6') for i in scored_order_v6]
        
        self.dns_push(domain, v4_addrs, v6_addrs)
        #self.log.info("VERDICT %s: No working servers, CNAME %s", domain, self.master_rotate)
    
    def dns_push(self, fqdn, v4_addrs, v6_addrs):
        """
        Push a set of A and AAAA records to the DNS
        """
        
        fqdn = fqdn + '.'
        
        update = dns.update.Update('aprs2.net', keyring=self.dns_keyring, keyalgorithm="hmac-sha256")
        update.delete(fqdn, 'a')
        update.delete(fqdn, 'aaaa')
        for a in v4_addrs:
            update.add(fqdn, 300, 'a', a.encode('ascii'))
        for a in v6_addrs:
            update.add(fqdn, 300, 'aaaa', a.encode('ascii'))
        
        try:
            response = dns.query.tcp(update, self.dns_master)
        except socket.error as e:
            self.log.error("DNS update error, cannot connect to DNS master: %r", e)
            return
        except dns.tsig.PeerBadKey as e:
            self.log.error("DNS update error, DNS master does not accept our key: %r", e)
            return
        except Exception as e:
            self.log.error("DNS update error: %r", e)
            return
            
        self.log.debug("Sent update, response: %r", response)
    
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

