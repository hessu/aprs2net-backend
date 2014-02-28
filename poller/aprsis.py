
"""
APRS-IS testing client for the purpose of checking if a server is working
"""

import socket
import re

re_login_ok = re.compile('# logresp ([^ ]+) ([^, ]+), server ([A-Z0-9\\-]+)')

class TCPPoll:
    def __init__(self, log):
        self.log = log
        self.sock_timeout = 5
        self.mycall = 'APRS2N-ET'
    
    def poll(self, host, port, serverid):
        """
        Test that an APRS-IS server is responsive
        """
        self.id = serverid
        self.host = host
        self.port = port
        
        self.log.info("%s: APRS-IS TCP test: %s port %s", self.id, host, port)
        
        if ':' in host:
            af = socket.AF_INET6
        else:
            af = socket.AF_INET
        
        s = socket.socket(af, socket.SOCK_STREAM)
        s.settimeout(self.sock_timeout)
        
        prompt = None
        login_ok = ""
        
        try:
            s.connect((host, port))
            prompt = s.recv(1024)
            self.log.debug('%s: Login prompt: %s', self.id, repr(prompt))
            s.send("user %s pass -1 vers aprs2net-poll 2.0\r\n" % self.mycall)
            login_ok = s.recv(1024)
            self.log.debug('%s: Login response: %s', self.id, repr(login_ok))
            s.close()
        except socket.error, msg:
            try:
                s.close()
            except Exception:
                pass
            s = None    
            return self.error("APRS-IS socket error: %s" % msg)
        
        s = None
        
        if prompt == "":
            return self.error('Server closed connection immediately without sending version string (ACL?)')
            
        m = re_login_ok.search(login_ok)
        if m == None:
            self.log.info('%s: Login response not recognized: %s', self.id, repr(login_ok))
            return self.error("APRS-IS login response line not recognized")
        
        my_back = m.group(1)
        verif_s = m.group(2)
        serverid_back = m.group(3)
        
        if my_back != self.mycall:
            return self.error("APRS-IS login response does not contain my callsign %s" % self.mycall)
        
        if verif_s != 'unverified':
            return self.error("APRS-IS login response is not 'unverified' for pass -1: got '%s'" % verif_s)
        
        if serverid_back != serverid:
            return self.error("APRS-IS login response for '%s' has unexpected server ID: '%s'" % (serverid, serverid_back))
        
        self.log.info("%s: APRS-IS TCP OK: %s port %s", self.id, host, port)
        
        return True
        
    def error(self, msg):
        self.log.info("%s: APRS-IS TCP FAIL: %s port %s: %s", self.id, self.host, self.port, msg)
        return msg

