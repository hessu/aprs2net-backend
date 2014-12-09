#!/usr/bin/python

import time
import threading
import logging
import logging.config
import ConfigParser
import sys
import traceback

import aprs2_redis
import aprs2_poll
import aprs2_config
import aprs2_logbuf

# All configuration variables need to be strings originally.
CONFIG_SECTION = 'poller'
DEFAULT_CONF = {
    # Site description
    'site_descr': 'Unconfigured, CC',
    
    # Server polling interval
    'poll_interval': '300',
    
    # Portal URL for downloading configs
    'portal_servers_url': 'https://portal-url.example.com/blah',
    'portal_rotates_url': 'https://portal-url.example.com/blah'
}

class Poller:
    """
    aprs2.net poller
    """
    def __init__(self, config_file='poller.conf'):
        # read logging config file
        logging.config.fileConfig(config_file)
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('main')
        self.log.info("Starting up")
        self.log_poller = logging.getLogger('poller')
        
        # read configuration
        self.config = ConfigParser.ConfigParser()
        self.config.add_section(CONFIG_SECTION)
        
        for option, value in DEFAULT_CONF.iteritems():
            self.config.set(CONFIG_SECTION, option, value)
            
        self.config.read(config_file)
        
        self.poll_interval = self.config.getint(CONFIG_SECTION, 'poll_interval')
        
        # config object for the web UI
        self.web_config = {
            'site_descr': self.config.get(CONFIG_SECTION, 'site_descr')
        }
        
        # redis client
        self.red = aprs2_redis.APRS2Redis()
        self.red.setWebConfig(self.web_config)
        self.config_manager = aprs2_config.ConfigManager(logging.getLogger('config'),
        	self.red,
        	self.config.get(CONFIG_SECTION, 'portal_servers_url'),
        	self.config.get(CONFIG_SECTION, 'portal_rotates_url'))
        self.config_manager.start()
        
        # thread limits
        self.threads_now = 0
        self.threads_max = 16
        self.threads = []
        
        # server software type cache
        self.software_type_cache = {}
        # cache for rate stats
        self.rates_cache = {}
        
        # IP address => server ID map
        self.address_map = {}
        self.address_map_refresh_t = 0
        self.address_map_refresh_int = 300
    
    def perform_poll(self, server):
        """
        Do the actual polling of a single server
        """
        
        # Use a separate log buffer for each poll, so that
        # we can store it in the database for easy lookup.
        log = aprs2_logbuf.PollingLog(self.log_poller)
        
        log.info("Poll thread started for %s", server['id'])
        p = aprs2_poll.Poll(log, server, self.red, self.software_type_cache, self.rates_cache, self.address_map)
        success = False
        try:
            success = p.poll()
        except Exception as ex:
            etype, value, tb = sys.exc_info()
            log.debug(''.join(traceback.format_exception(etype, value, tb)))
            p.error('crash', 'Poller crashed: %r' % ex)
        
        props = p.properties
        now = int(time.time())
        
        state = self.red.getServerStatus(server['id'])
        if state == None:
            state = {}
        
        prev_status = state.get('status')
        
        if success == True:
            state['status'] = 'ok'
            state['props'] = props
        else:
            state['status'] = 'fail'
            if props:
                state['props'] = props
            else:
                old_props = state.get('props', {})
                if old_props:
                    keep_props = {}
                    for i in ('type', 'soft', 'vers', 'os', 'id'):
                        keep_props[i] = old_props.get(i)
                    state['props'] = keep_props
        
        if state['status'] != prev_status or 'last_change' not in state:
            state['last_change'] = now
        
        # update availability statistics
        if server.get('out_of_service', False):
            log.info("%s: Server is marker do be out of service, not updating availability statistics", server['id'])
        else:
            if 'last_test' in state:
                tdif = now - state['last_test']
                if tdif > 0 and tdif < self.poll_interval * 3:
                    state['avail_3'], state['avail_30'] = self.red.updateAvail(server['id'], tdif, state['status'] == 'ok')
        
        state['errors'] = p.errors
        state['last_test'] = now
        
        self.red.setServerStatus(server['id'], state)
        self.red.storeServerLog(server['id'], { 't': now, 'log': log.buffer_string() })
        self.red.sendServerStatusMessage({ 'config': server, 'status': state })
        
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
            if server and not server.get('deleted'):
                self.red.setPollQ(i, int(time.time()) + self.poll_interval)
                self.poll(server)
            else:
                self.log.info("Server %s has been removed, removing from queue.", i)
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
    
    def load_address_map(self):
        """
        Get a new address map, if necessary
        """
        now = time.time()
        if now > self.address_map_refresh_t or now < self.address_map_refresh_t - self.address_map_refresh_int:
            self.log.info("Refreshing address map")
            # Get a fresh address map
            self.address_map = self.red.getAddressMap()
            self.address_map_refresh_t = now + self.address_map_refresh_int
        
    
    def loop(self):
        """
        Main polling loop
        """
        
        while True:
            # consider reloading address_map
            self.load_address_map()
            
            # reap old threads
            self.loop_reap_old_threads()
            
            # start up new poll rounds, if thread limit allows
            if self.threads_now < self.threads_max:
                self.loop_consider_polls()
            time.sleep(1)


cfgfile = 'poller.conf'
if len(sys.argv) > 1:
    cfgfile = sys.argv[1]

poller = Poller(cfgfile)
poller.loop()

