
import time
import requests
import json

class Poll:
    def __init__(self, log, server):
        self.log = log
        self.server = server
        self.id = server['id']
        self.status_url = 'http://%s:14501/' % self.server['ip4']
        self.rhead = {'User-agent': 'aprs2net-poller/2.0'}
        
        self.try_order = ['aprsc', 'javap4', 'javap3']
        
        self.http_status_timing = None
        self.properties = {}
        
        self.errors = []
    
    def error(self, msg):
        """
        Push an error to the list of errors
        """
        
        self.log.info("%s: Polling error: %s", self.id, msg)
        self.errors.append(msg)
        return False
    
    def poll(self):
        """
        Run a polling round.
        """
        self.log.info("polling %s", self.id)
        self.log.debug("config: %r", self.server)
        
        ok = False
        for t in self.try_order:
            r = False
            
            if t == 'aprsc':
                r = self.poll_aprsc()
            if t == 'javap4':
                r = self.poll_javaprssrvr4()
            if t == 'javap3':
                r = self.poll_javaprssrvr3()
            
            if r == None:
                continue
            if r == False:
                return False
            
            self.log.info("%s: test %s ok", self.id, t)
            ok = True
            break
        
        if ok == False:
            self.log.error("Unrecognized server: %r", self.id)
            return False
                
        return self.service_tests()
    
    def poll_javaprssrvr3(self):
        """
        Poll javAPRSSrvr 3.x
        """
    
        # get front page, figure out which server type it is
        t_start = time.time()
        r = requests.get(self.status_url, headers=self.rhead)
        self.log.debug("%s: front %r", self.id, r.status_code)
        
        http_server = r.headers.get('server')
        if http_server != None:
            self.log.info("%s: Reports Server: %r - not javAPRSSrvr 3.x!", self.id, http_server)
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        
        if "javAPRSSrvr 3." not in d and "Pete Loveall AE5PL" not in d:
            self.log.info("%s: HTML does not mention javAPRSSrvr 3 or Pete", self.id)
            return False
            
        self.http_status_timing = t_dur
        self.log.debug("%s: parsing javAPRSSrvr 3.x HTML", self.id)
        return True
    
    def poll_javaprssrvr4(self):
        """
        Get aprsc's status.json
        """
        
        t_start = time.time()
        r = requests.get('%s%s' % (self.status_url, 'detail.xml'), headers=self.rhead)
        self.log.debug("%s: detail.xml %r", self.id, r.status_code)
        
        if r.status_code == 404:
            return None
            
        if r.status_code != 200:
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        self.http_status_timing = t_dur
        
        return True
        
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        t_start = time.time()
        r = requests.get('%s%s' % (self.status_url, 'status.json'), headers=self.rhead)
        self.log.debug("%s: status.json %r", self.id, r.status_code)
        
        if r.status_code == 404:
            return None
            
        if r.status_code != 200:
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        self.http_status_timing = t_dur
        
        try:
            j = json.loads(d)
        except Exception as e:
            self.log.info("%s: JSON parsing failed: %r", self.id, e)
            return self.error('aprsc status.json JSON parsing failed')
        
        return self.parse_aprsc(j)
        
    def parse_aprsc(self, j):
        """
        Parse aprsc status JSON
        """
        
        j_server = j.get('server')
        if j_server == None:
            return self.error('aprsc status.json does not have a server block')
        
        self.properties['soft'] = j_server.get('software')
        self.properties['vers'] = j_server.get('software_version')
        
        return True

    def service_tests(self):
        """
        Perform APRS-IS service tests
        """
        
        self.log.debug("%s: HTTP OK %.3f s", self.id, self.http_status_timing)
        