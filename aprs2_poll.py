
import requests

class Poll:
    def __init__(self, log, server):
        self.log = log
        self.server = server
        self.id = server['id']
        self.status_url = 'http://%s:14501/' % self.server['ip4']
    
    def poll(self):
        """
        Run a polling round.
        """
        self.log.info("polling %s", self.id)
        self.log.debug("config: %r", self.server)
        
        # get front page, figure out which server type it is
        front = requests.get(self.status_url)
        http_server = front.headers.get('server')
        self.log.debug("%s: front %r (%s)", self.id, front.status_code, http_server)
        
        if http_server == None:
            self.parse_javaprssrvr_html(front)
        else:
            if http_server.startswith('aprsc'):
                self.poll_aprsc()
    
    def parse_javaprssrvr_html(self, front):
        """
        Parse the front page HTML returned by javaprssrvr
        """
        return
    
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        js = requests.get('%s%s' % (self.status_url, 'status.json'))
        self.log.debug("%s: status.json %r", self.id, js.status_code)
        