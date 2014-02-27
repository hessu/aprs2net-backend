
import requests
import json

# this is a set of servers to poll, for testing purposes, just to get us started.
test_setup = {
    'T2FINLAND': {
        'host': 'finland',
        'ip4': '85.188.1.32',
        'ip6': '2001:67c:15c:1::32'
    },
    'T2BRAZIL': {
        'host': 'brazil',
        'ip4': '75.144.65.121'
    },
    'T2HAM': {
        'host': 'amsterdam',
        'ip4': '195.90.121.18',
        'ip6': '2a01:348::4:0:34:1:1'
    },
    'T2APRSNZ': {
        'host': 'aprsnz',
        'ip4': '125.236.199.246'
    },
    'T2ARK': {
        'host': 'arkansas',
        'ip4': '67.14.192.41'
    },
    'T2AUSTRIA': {
        'host': 'austria',
        'ip4': '194.208.144.169'
    },
    'T2BASEL': {
        'host': 'basel',
        'ip4': '185.14.156.135',
        'ip6': '2a03:b240:100::1'
    },
    'T2BC': {
        'host': 'bc',
        'ip4': '206.12.104.10'
    },
    'T2BELGIUM': {
        'host': 'belgium',
        'ip4': '193.190.240.225'
    }
}

class ConfigManager:
    def __init__(self, log, red):
        self.log = log
        self.red = red
        
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
                self.red.setPollQ(i, int(time.time()))
    
