
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
        
        # poll time, in seconds (float), per address family ("ip4", "ip6")
        self.poll_t_14580 = {}
        
        # http status poll time
        self.http_status_t = None
        
    def score(self):
        """
        Calculate and return a total score. Best: 0, higher is worce.
        """
        
        score = 0
        
        #
        # HTTP
        #
        
        if self.http_status_t == None:
            return self.score_max
            
        score += max(0, self.http_status_t - self.rtt_good_enough) * self.http_rtt_mul
        
        #
        # APRS-IS
        #
        
        is_score = 0
        if len(self.poll_t_14580) < 1:
            return self.score_max
        
        for k in self.poll_t_14580:
            t = self.poll_t_14580.get(k, 200)
            is_score += max(0, t - self.rtt_good_enough) * self.aprsis_rtt_mul
        
        is_score = is_score / len(self.poll_t_14580)
        score += is_score
        
        return score
        


        
        
    