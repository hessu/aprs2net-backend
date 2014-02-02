#!/usr/bin/python

import time
import aprs2_redis

pollInterval = 10

test_setup = {
    'T2FINLAND': {
        'ip4': '85.188.1.32',
        'ip6': '2001:67c:15c:1::32'
    }
}

class Poller:
    """
    aprs2.net poller
    """
    def __init__(self):
    	self.red = aprs2_redis.APRS2Redis()

    def test_load(self, set):
        """
        Load a set of servers in Redis for testing
        """
        
        print "Loading test set:"
        for i in test_setup.keys():
            print " ... %r" % i
            s = test_setup[i]
            s['id'] = i
            
            self.red.storeServer(s)
            self.red.setPollQ(i, time.time())
        
    def poll(self, server):
        """
        Poll a single server
        """
        print "Polling: %s" % server['id']
    
    def loop(self):
        

        while True:
            to_poll = self.red.getPollSet()
            
            if to_poll:
                for i in to_poll:
                    server = self.red.getServer(i)
                    if server:
                        self.red.setPollQ(i, time.time() + pollInterval)
                        self.poll(server)
                    else:
                        self.red.delPollQ(i)
                
            time.sleep(2)


poller = Poller()
poller.test_load(test_setup)
poller.loop()