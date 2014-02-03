
import requests

class Poll:
    def __init__(self, log, server):
        self.log = log
        self.server = server
        self.id = server['id']
        self.status_url = 'http://%s:14501/' % self.server['ip4']
        self.rhead = {'User-agent': 'aprs2net-poller/2.0'}
    
    def poll(self):
        """
        Run a polling round.
        """
        self.log.info("polling %s", self.id)
        self.log.debug("config: %r", self.server)
        
        if self.poll_aprsc():
            return
        
        if self.poll_javaprssrvr4():
            return
            
        if self.poll_javaprssrvr3():
            return
        
        self.log.error("Unrecognized server: %r", self.id)
            
    
    def poll_javaprssrvr3(self):
        """
        Poll javAPRSSrvr 3.x
        """
    
        # get front page, figure out which server type it is
        front = requests.get(self.status_url, headers=self.rhead)
        self.log.debug("%s: front %r", self.id, front.status_code)
        
        http_server = front.headers.get('server')
        if http_server != None:
            self.log.info("%s: Reports Server: %r - not javAPRSSrvr 3.x!", self.id, http_server)
            return False
        
        d = front.content
        if "javAPRSSrvr 3." not in d and "Pete Loveall AE5PL" not in d:
            return False
            
        self.log.debug("%s: parsing javAPRSSrvr 3.x HTML", self.id)
        return True
    
    def poll_javaprssrvr4(self):
        """
        Get aprsc's status.json
        """
        
        r = requests.get('%s%s' % (self.status_url, 'detail.xml'), headers=self.rhead)
        self.log.debug("%s: detail.xml %r", self.id, r.status_code)
        
        if r.status_code != 200:
            return False
        
        return True
        
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        r = requests.get('%s%s' % (self.status_url, 'status.json'), headers=self.rhead)
        self.log.debug("%s: status.json %r", self.id, r.status_code)
        
        if r.status_code != 200:
            return False
        
        return True
        
