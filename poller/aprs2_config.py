
"""
Config download manager. Downloads JSON server configurations from the portal
and maintains current list of servers in the Redis database.

Runs a maintenance thread, so that these operations won't block the polling
operations, even if the portal would be down.
"""

import requests
import json
import threading
import time
import random
import re

POLL_INTERVAL = 2*60

# this is a set of servers to poll, for testing purposes, just to get us started.
test_setup = {
    'T2FINLAND': {
        'host': 'finland',
        'ipv4': '85.188.1.32',
        'ipv6': '2001:67c:15c:1::32'
    },
    'T2BRAZIL': {
        'host': 'brazil',
        'ipv4': '75.144.65.121'
    },
    'T2HAM': {
        'host': 'amsterdam',
        'ipv4': '195.90.121.18',
        'ipv6': '2a01:348::4:0:34:1:1'
    },
    'T2APRSNZ': {
        'host': 'aprsnz',
        'ipv4': '125.236.199.246'
    },
    'T2BASEL': {
        'host': 'basel',
        'ipv4': '185.14.156.135',
        'ipv6': '2a03:b240:100::1'
    },
}

class ConfigManager:
    def __init__(self, log, red, portal_servers_url, portal_rotates_url, unmanaged_rotates = {}, credentials = None):
        self.log = log
        self.red = red
        
        self.rhead = {'User-agent': 'aprs2net-ConfigManager/2.0'}
        self.http_timeout = 30
        
        self.portal_servers_url = portal_servers_url
        self.portal_rotates_url = portal_rotates_url
        self.config_etag = None
        self.unmanaged_rotates = unmanaged_rotates
        self.client_credentials = credentials
        
        self.shutdown = False
        
        self.log.info("ConfigManager initialized")
    
    def start(self):
        """
        Start background thread for config management
        """
        self.cfg_thread = threading.Thread(target=self.cfg_loop)
        self.cfg_thread.daemon = True
        self.cfg_thread.start()
        self.log.info("ConfigManager thread started")
    
    def cfg_loop(self):
        """
        Main configuration thread loop.
        """
        
        while not self.shutdown:
            # Make sure the configuration manager thread does not die
            # permanently due to a spurious error.
            try:
                self.refresh_config()
            except Exception as e:
                self.log.error("ConfigManager refresh_config crashed: %r", e)
                
            time.sleep(POLL_INTERVAL)
    
    def login(self, user, passwd):
        """
        Try user/password auth
        """
        
        self.log.info("Logging in to portal ...")
        url = 'https://t2sysop.aprs2.net/accounts/login/'
        s = requests.Session()
        try:
            r = s.get(url, timeout=self.http_timeout, verify=True)
            r.raise_for_status()
            d = r.content
        except Exception as e:
            self.log.error("Portal login step 1: %s - Connection error: %r", url, e)
            return None
        
        # <input type='hidden' name='csrfmiddlewaretoken' value='f03Wi2WTERnpMx1Po8nweb2ySuU1oJ4U' />
        csrf_re = re.compile("input .*name='csrfmiddlewaretoken'.*value='(.*?)'")
        match = csrf_re.search(d)
        if match == None:
            self.log.error("Portal login: CSRF token not found")
            return
        csrftoken = match.group(1)
        
        #self.log.debug("Login: CSRF token: '%s'", csrftoken)
        #self.log.debug("Cookies: %r", s.cookies)
        
        s.headers = {'Referer': url}
        login_payload = { 'username': user, 'password': passwd, 'csrfmiddlewaretoken': csrftoken, 'next': '' }
        try:
            r = s.post(url, data=login_payload, timeout=self.http_timeout, verify=True)
            r.raise_for_status()
            d = r.content
            #print d
        except Exception as e:
            self.log.error("Portal login step 2: %s - Connection error: %r", url, e)
            return None
        
        return s
    
    def fetch_config(self, url, etag=None, session=None):
        """
        Fetch one config object
        """
        self.log.info("Fetching %s", url)
        
        if session == None:
            session = requests.Session()
        
        t_start = time.time()
        try:
            req_headers = self.rhead.copy()
            if self.config_etag:
                req_headers['If-None-Match'] = self.config_etag
            r = session.get(url, headers=req_headers, timeout=self.http_timeout, verify=True, cert=self.client_credentials)
            r.raise_for_status()
            d = r.content
        except Exception as e:
            self.log.error("Portal: %s - Connection error: %r", url, e)
            return (False, None)
            
        t_end = time.time()
        t_dur = t_end - t_start
        
        if r.status_code == 304:
            self.log.info("Portal: %s - %r: Not modified (cache hit)", url, r.status_code)
            return (False, None)
        
        if r.status_code != 200:
            self.log.error("Portal: %s - Failed download, code: %r", url, r.status_code)
            return (False, None)
        
        new_etag = r.headers.get('etag')
        if new_etag != None and new_etag == self.config_etag:
            self.log.info("Portal: Cache hit for config etag %r, no need to process new config", new_etag)
            return (False, new_etag)
            
        self.log.info("Portal: Got new etag %r", new_etag)
        
        try:
            j = json.loads(d)
        except Exception as e:
            self.log.error("Portal: JSON parsing failed (%s): %r", url, e)
            return (False, None)
        
        return (j, new_etag)
    
    def refresh_config(self):
        """
        Fetch configuration from the portal
        """
        
        self.log.info("Fetching current server list from portal...")
        
        j, new_etag = self.fetch_config(self.portal_rotates_url, self.config_etag)
        if j == False:
            return False
        
        self.config_etag = new_etag
        
        return self.process_config_json(j)
    
    def process_config_json(self, j):
        """
        Process the contents of a downloaded server config JSON
        """
        
        polled = {}
        uniqs = {}
        members = {}
        
        for rid in j:
            #self.log.debug("rotate %s", rid)
            
            if rid.startswith('t2poll'):
                continue
            
            rotate = j.get(rid)
            servers = rotate.get('servers')
            del rotate['servers']
            
            members[rid] = []
            
            for id in servers:
                #self.log.debug("  server %s", id)
                if id.startswith('T2POLL-'):
                    continue
                new = servers.get(id)
                members[rid].append(id)
                old = uniqs.get(id)
                if old != None:
                    old['member'].append(rid)
                else:
                    new['member'] = [ rid ]
                    uniqs[id] = new
                    
            rotate['members'] = members[rid]
            self.red.storeRotate(rid, rotate)
        
        self.log.info("Saving servers...")
        
        # collect a mapping from IP address to server ID, for later
        # figuring out the id of an uplink server
        addr_map = {}
        
        for id in uniqs:
            c = uniqs.get(id)
            id = id.upper()
            #self.log.debug("%s: %r", id, c)
            c['id'] = id
            
            # We do not support IPv6-only servers for now.
            ip4 = c.get('ipv4')
            ip6 = c.get('ipv6')
            if ip4 == None:
                self.log.info("Server has no IPv4 address: %s", id)
                self.red.delPollQ(id)
                self.red.delServer(id)
                continue
            
            for m in c.get('member', []):
                if not m in self.unmanaged_rotates:
                    c['show_members'] = 1;
            
            self.red.storeServer(c)
            polled[id] = 1
            
            addr_map[ip4] = id
            if ip6 != None:
            	addr_map[ip6] = id
            
            if c.get('deleted'):
                #self.log.info("Server is marked as deleted, removing from poll queue: %s", id)
                self.red.delPollQ(id)
            else:
                if self.red.getPollQ(id) == None:
                    self.log.info("Adding server in poll queue: %s", id)
                    self.red.setPollQ(id, int(time.time()) + random.randint(0,300))
        
        # TODO: add sanity check for too few servers
        
        self.red.setAddressMap(addr_map)
        self.log.info("Applied new server configuration")
        self.pollq_cleanup(polled)
    
    def pollq_cleanup(self, polled):
        """
        Remove deleted servers from poll queue
        """
        
        pollq = self.red.getPollList()
        for id in pollq:
            if not id in polled:
                self.log.info("Removing deleted server from polling queue: %s", id)
                self.red.delPollQ(id)
        
        for id in self.red.getServerIds():
            if not id in polled:
                self.red.delServer(id)
        
    def test_load(self, set):
        """
        Load a set of servers in Redis for testing
        """
        
        self.log.info("Loading test set...")
        for i in test_setup.keys():
            self.log.info(" ... %r", i)
            s = test_setup[i]
            s['id'] = i
            
            self.red.storeServer(s)
            if i == 'T2BRAZIL':
                self.red.setPollQ(i, 0)
            else:
                self.red.setPollQ(i, int(time.time()) + random.randint(0,30))
    
