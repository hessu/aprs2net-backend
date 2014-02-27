
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
        # 30: rtt of 2.4 seconds will add 60 to score.
        self.http_rtt_mul = 30
        
        # Multiply the TCP APRS-IS score by N before adding to score.
        # It will be divided by the number of APRS-IS ports successfully polled (ipv4, ipv6: 2)
        self.aprsis_rtt_mul = 30
        
        # poll time, in seconds (float), per address family ("ipv4", "ipv6")
        self.poll_t_14580 = {}
        
        # http status poll time
        self.http_status_t = None
        
        self.score = 0
        self.score_components = {}
    
    def score_add(self, type, val):
        self.score += val
        self.score_components[type] = val
    
    def round_components(self):
        for i in self.score_components:
           if self.score_components[i] > 0.0:
               self.score_components[i] = int(self.score_components[i] * 10) / 10.0
        
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
        
        self.score_add('http_rtt', max(0, self.http_status_t - self.rtt_good_enough) * self.http_rtt_mul)
        
        #
        # APRS-IS
        #
        
        # We need at least one address family (ipv4, ipv6) working.
        if len(self.poll_t_14580) < 1:
            return self.score_max
        
        # Calculate an arithmetic average score based on 14580 RTT.
        is_score = 0
        for k in self.poll_t_14580:
            t = self.poll_t_14580.get(k, 30) # default 30 seconds, if not found (should not happen)
            is_score += max(0, t - self.rtt_good_enough) * self.aprsis_rtt_mul
        
        is_score = is_score / len(self.poll_t_14580)
        self.score_add('14580_rtt', is_score)
        
        #
        # Amount of users
        #
        
        # Find the worst case load
        loads = [ props.get('worst_load', 100) ]
        
        load = max(loads)
        self.score_add('user_load', load*10.0)
        
        self.round_components()
        
        return self.score
        


        
        
    