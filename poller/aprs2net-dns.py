#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import traceback

import aprs2_config

# All configuration variables need to be strings originally.
CONFIG_SECTION = 'dns'
DEFAULT_CONF = {
    # Server polling interval
    'poll_interval': '60',
    
    # Portal URL for downloading configs
    'portal_base_url': 'https://home.tomh.us:8001'
}

class DNSDriver:
    """
    aprs2.net DNS driver
    """
    def __init__(self, config_file='poller.conf'):
        # read logging config file
        logging.config.fileConfig(config_file)
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('main')
        self.log.info("Starting up")
        self.log_poller = logging.getLogger('dns')
        
        # read configuration
        self.config = ConfigParser.ConfigParser()
        self.config.add_section(CONFIG_SECTION)
        
        for option, value in DEFAULT_CONF.iteritems():
            self.config.set(CONFIG_SECTION, option, value)
            
        self.config.read(config_file)
        
        self.dns_master = self.config.get(CONFIG_SECTION, 'dns_master')
        self.poll_interval = self.config.getint(CONFIG_SECTION, 'poll_interval')
        self.domains = self.config.get(CONFIG_SECTION, 'domains').split(',')
        self.pollers = self.config.get(CONFIG_SECTION, 'pollers').split(' ')
        
driver = DNSDriver()
driver.loop()

