#!/usr/bin/python

import time
import threading
import logging
import logging.config

import aprs2_redis
import aprs2_poll

pollInterval = 300

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

class Poller:
    """
    aprs2.net poller
    """
    def __init__(self):
        self.red = aprs2_redis.APRS2Redis()
        
        # read logging config file
        logging.config.fileConfig('logging.conf')
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('poller')
        self.log.info("Starting up")
        
        # thread limits
        self.threads_now = 0
        self.threads_max = 4
        self.threads = []
        
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
    
    def perform_poll(self, server):
    	"""
    	Do the actual polling of a single server
    	"""
    	
    	self.log.info("Poll thread started for %s", server['id'])
    	p = aprs2_poll.Poll(self.log, server)
    	success = p.poll()
    	props = p.properties
    	
    	now = int(time.time())
    	
    	state = self.red.getServerStatus(server['id'])
    	if state == None:
    	    state = {}
    	
    	if success == True:
    	    state['status'] = 'ok'
    	    state['last_ok'] = now
    	    state['props'] = props
    	else:
    	    state['status'] = 'fail'
    	    if props:
    	        state['props'] = props
    	    
        state['errors'] = p.errors
    	state['last_test'] = now
    	
    	self.red.setServerStatus(server['id'], state)
    	
        
    def poll(self, server):
        """
        Poll a single server
        """
        self.threads_now += 1
        
        thread = threading.Thread(target=self.perform_poll, args=(server,))
        thread.start()
        self.threads.append(thread)
    
    def loop_consider_polls(self):
        """
        Check if there are servers to poll in the schedule,
        start polls as necessary, while obeying the thread limit.
        """
        
        to_poll = self.red.getPollSet()
        
        if to_poll:
            self.log.info("Scheduled polls: %r", to_poll)
        
        while to_poll and self.threads_now < self.threads_max:
            i = to_poll.pop(0)
            server = self.red.getServer(i)
            if server:
                self.red.setPollQ(i, int(time.time()) + pollInterval)
                self.poll(server)
            else:
                self.log.info("Server %s has been deleted, removing from queue.", i)
                self.red.delPollQ(i)
    
    def loop_reap_old_threads(self):
        """
        Check which threads are still running.
        """
        
        threads_left = []
        
        for th in self.threads:
            #self.log.debug("* checking thread %d", th.ident)
            if not th.is_alive():
                self.log.debug("* thread %d has finished", th.ident)
                th.join()
                #self.log.debug("* thread %d joined", th.ident)
                self.threads_now -= 1
            else:
                threads_left.append(th)
                
        self.threads = threads_left
    
    def loop(self):
        """
        Main polling loop
        """
	
        while True:
            # reap old threads
            self.loop_reap_old_threads()
            
            # start up new poll rounds, if thread limit allows
            if self.threads_now < self.threads_max:
                self.loop_consider_polls()
            
            time.sleep(1)

poller = Poller()
poller.test_load(test_setup)
poller.loop()

