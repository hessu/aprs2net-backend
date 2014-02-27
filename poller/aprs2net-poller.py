#!/usr/bin/python

import time
import threading
import logging
import logging.config

import aprs2_redis
import aprs2_poll
import aprs2_config
import aprs2_logbuf

pollInterval = 300

class Poller:
    """
    aprs2.net poller
    """
    def __init__(self):
        # read logging config file
        logging.config.fileConfig('logging.conf')
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('main')
        self.log.info("Starting up")
        self.log_poller = logging.getLogger('poller')
        
        self.red = aprs2_redis.APRS2Redis()
        self.config_manager = aprs2_config.ConfigManager(logging.getLogger('config'), self.red)
        
        # thread limits
        self.threads_now = 0
        self.threads_max = 16
        self.threads = []
        
        # server software type cache
        self.software_type_cache = {}
        
    def perform_poll(self, server):
    	"""
    	Do the actual polling of a single server
    	"""
    	
    	# Use a separate log buffer for each poll, so that
    	# we can store it in the database for easy lookup.
    	log = aprs2_logbuf.PollingLog(self.log_poller)
    	
    	log.info("Poll thread started for %s", server['id'])
    	p = aprs2_poll.Poll(log, server, self.software_type_cache)
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
    	self.red.storeServerLog(server['id'], { 't': now, 'log': log.buffer_string() })
    	
        
    def poll(self, server):
        """
        Poll a single server
        """
        self.threads_now += 1
        
        thread = threading.Thread(target=self.perform_poll, args=(server,))
        thread.daemon = True
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
poller.loop()

