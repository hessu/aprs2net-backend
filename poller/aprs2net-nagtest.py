#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import os

import aprs2_redis

RET_OK = 0
RET_WARNING = 1
RET_CRITICAL = 2
RET_UNKNOWN = 3


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


class NagiosTest:
    """
    aprs2.net nagios service test
    """
    def __init__(self):
        # redis client
        self.red = aprs2_redis.APRS2Redis(db=1)

    def check(self, id):
        s = self.red.getServerStatus(id)
        ret = RET_UNKNOWN
        
        result_prefix = 'IS UNKNOWN'
        result_suffix = []
        
        if s == None:
            ret = RET_UNKNOWN
            result_prefix = 'IS server not known'
            result_suffix.append('%s not in redis database' % id)
        elif s.get('status') == 'ok':
           ret = RET_OK
           result_prefix = 'IS OK'
           
           props = s.get('props', {})
           if 'clients' in props:
           	result_suffix.append('%d clients' % props.get('clients', -1))
           if 'uptime' in props:
           	result_suffix.append('uptime %s' % dur_str(props.get('uptime', 0)))
           if 'soft' in props:
           	result_suffix.append('%s %s' % (props.get('soft'), props.get('vers', '')))
           
        elif s.get('status') == 'fail':
           ret = RET_CRITICAL
           result_prefix = 'IS CRITICAL'
           for e in s.get('errors', []):
               code, msg = e
               result_suffix.append(msg)
        
        print("%s - %s" % (result_prefix, ', '.join(result_suffix)))
        #print "%r" % s
        sys.exit(ret)
        

serverid = None
if len(sys.argv) > 1:
    serverid = sys.argv[1]
else:
    print "Usage: aprs2net-nagtest T2SERVERID"
    sys.exit(1)
    
t = NagiosTest()
t.check(serverid)

