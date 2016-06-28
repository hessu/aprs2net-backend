
from distutils.version import LooseVersion

def dur_str(i):
    s = ''
    
    if i >= 86400:
        d = i / 86400
        i -= d * 86400
        s += '%dd' % d
        
    if i >= 3600:
        d = i / 3600
        i -= d * 3600
        s += '%dh' % d
        
    if i >= 60:
        d = i / 60
        i -= d * 60
        s += '%dm' % d
        
    if i > 0 or s == '':
        s += '%.0fs' % i
    
    return s

class Score:
    """
    The Score class is used to collect measurements from a server and derive a total score.
    """
    def __init__(self):
        # Maximum score
        self.score_max = 1000
        
        # For each polling time, we set the added score to 0 if the rtt is
        # "good enough" in an attempt to make the playing field level.
        # In seconds.
        self.rtt_good_enough = 0.4
        
        # Multiply the HTTP RTT by N before adding to score.
        # 50: rtt of 2.4 seconds will add 60 to score.
        self.http_rtt_mul = 50
        
        # Multiply the TCP APRS-IS rtt by N before adding to score.
        # It will be divided by the number of APRS-IS ports successfully polled (ipv4, ipv6: 2)
        self.aprsis_rtt_mul = 40
        
        # Uplink uptime penalty time range, in seconds.
        # If uplink has been established recently, it is sometimes a sign that
        # the uplink is unstable and flapping due to a bad network connection.
        # Give a bit of penalty for 0 ... N seconds of uplink uptime.
        self.uplink_uptime_penalty_time = 900 # 15 minutes
        
        # Too old server software version gives penalty.
        # Configure the map key as minimum software version that is "new enough",
        # value is the score penalty given to versions older than this.
        # TODO: make configurable from config file.
        self.version_penalty = {
        	'aprsc': { '2.0.18': 400 }
        }
        
        # poll time, in seconds (float), per address family ("ipv4", "ipv6")
        self.poll_t_14580 = {}
        
        # http status poll time
        self.http_status_t = None
        
        self.score = 0
        self.score_components = {}
    
    def score_add(self, type, val, string):
        self.score += val
        self.score_components[type] = [ val, string ]
    
    def round_components(self):
        for i in self.score_components:
           if self.score_components[i][0] > 0.0:
               self.score_components[i][0] = int(self.score_components[i][0] * 10) / 10.0
        
    def get(self, props):
        """
        Calculate and return a total score. Best: 0, higher is worce.
        """
        
        #
        # HTTP
        #
        
        # We must have a working HTTP status.
        if self.http_status_t == None:
            return self.score_max
        
        self.score_add('http_rtt', max(0, self.http_status_t - self.rtt_good_enough) * self.http_rtt_mul,
        	'%.3f s' % self.http_status_t )
        
        #
        # APRS-IS
        #
        
        # We need at least one address family (ipv4, ipv6) working.
        if len(self.poll_t_14580) < 1:
            return self.score_max
        
        # Calculate an arithmetic average score based on 14580 RTT.
        is_score = 0
        rtt_sum = 0
        for k in self.poll_t_14580:
            t = self.poll_t_14580.get(k, 30) # default 30 seconds, if not found (should not happen)
            rtt_sum += t
            is_score += max(0.0, t - self.rtt_good_enough) * self.aprsis_rtt_mul
        
        is_score = is_score / len(self.poll_t_14580)
        rtt_avg = rtt_sum / len(self.poll_t_14580)
        self.score_add('aprsis_rtt', is_score,
        	'%.3f s' % rtt_avg)
        
        #
        # Amount of users
        #
        
        # Find the worst case load
        loads = [ props.get('worst_load', 100) ]
        
        load = max(loads)
        self.score_add('user_load', load*10.0, '%.1f %%' % load)
        
        self.round_components()
        
        #
        # Uptime
        #
        # If the server's uptime is low, it might be in a crashing loop or
        # unstable - low amount of users, but gets a very good score!
        # Give a bit of penalty for newly rebooted servers.
        uptime = props.get('uptime')
        if uptime != None:
            score_range = 30.0*60.0 # 30 minutes
            uptime_max_penalty = 500.0
            if uptime < 0:
                uptime = 0
            if uptime < score_range:
            	penalty = (score_range - uptime) / score_range * uptime_max_penalty
            	uptime_s = dur_str(uptime)
                self.score_add('uptime', penalty, uptime_s)
        
        #
        # Uplink uptime
        #
        # If the server's uplink has only been up for a short while, it may be
        # that it's flapping up and down. Give a bit of penalty.
        ups = props.get('uplinks', [])
        
        if len(ups) > 0:
            upl = ups[0]
            uplink_uptime = upl.get('up', 0)
            if uplink_uptime < self.uplink_uptime_penalty_time:
                penalty = self.uplink_uptime_penalty_time - uplink_uptime
                self.score_add('uplink_uptime', penalty, dur_str(uplink_uptime))
        
        #
        # Server software version
        #
        # If there is a minimum software version configured for the server software,
        # and the server runs a version older than that, give the given penalty.
        server_sw = str(props.get('soft'))
        server_ver = str(props.get('vers'))
        if server_sw and server_ver:
            reqs = self.version_penalty.get(server_sw, {})
            for req_ver in reqs:
               if LooseVersion(server_ver) < LooseVersion(req_ver):
                   penalty = reqs.get(req_ver, 1)
                   self.score_add('version', penalty, server_ver)
        
        return self.score

