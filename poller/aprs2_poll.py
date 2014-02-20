
import time
import requests
import json
import re
from lxml import etree

import aprsis
import aprs2_score

# compile regular expressions to make them run faster
javap3_re = {
    'id': re.compile('<TD[^>]*>Server ID</TD><TD>([^>]+)</TD>'),
    'os': re.compile('<TD[^>]*>OS</TD><TD>([^>]+)</TD>'),
    'soft': re.compile('<TH[^>]*>(javAPRSSrvr) \\d+.\\d+[^>]+<BR>'),
    'vers': re.compile('<TH[^>]*>javAPRSSrvr (\\d+.\\d+[^>]+)<BR>'),
}

javap3_re_num = {
    # depending on server's system locale these integers have thousands separators, or not, either '.' or ','
    'clients': re.compile('<TD[^>]*>Current Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'clients_max': re.compile('<TD[^>]*>Maximum Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'total_bytes_in': re.compile('<TD[^>]*>Total Bytes In</TD><TD>([\\d,\\.]+)</TD>'),
    'total_bytes_out': re.compile('<TD[^>]*>Total Bytes Out</TD><TD>([\\d,\\.]+)</TD>'),
}

class Poll:
    def __init__(self, log, server):
        self.log = log
        self.server = server
        self.id = server['id']
        self.status_url = 'http://%s:14501/' % self.server['ip4']
        self.rhead = {'User-agent': 'aprs2net-poller/2.0'}
        self.http_timeout = 5.0
        
        self.try_order = ['aprsc', 'javap4', 'javap3']
        
        self.properties = {}
        
        self.score = aprs2_score.Score()
        
        self.errors = []
    
    def error(self, msg):
        """
        Push an error to the list of errors
        """
        
        self.log.info("%s: Polling error: %s", self.id, msg)
        self.errors.append(msg)
        return False
    
    def poll(self):
        """
        Run a polling round.
        """
        self.log.info("polling %s", self.id)
        self.log.debug("config: %r", self.server)
        
        ok = False
        for t in self.try_order:
            r = False
            
            if t == 'aprsc':
                r = self.poll_aprsc()
            if t == 'javap4':
                r = self.poll_javaprssrvr4()
            if t == 'javap3':
                r = self.poll_javaprssrvr3()
            
            if r == None:
                continue
            if r == False:
                return False
            
            self.log.debug("%s: HTTP %s OK %.3f s", self.id, t, self.score.http_status_t)
            
            if self.check_properties() == False:
                return False
                
            self.log.debug("%s: Server users %d/%d (%.1f %%)",
                self.id, self.properties['clients'], self.properties['clients_max'], self.properties['user_load'])
                
            ok = True
            break
        
        if ok == False:
            self.log.error("Unrecognized server: %r", self.id)
            return False
        
        if not self.service_tests():
            return False
        
        self.properties['score'] = self.score.score()
        self.log.info("%s: Server OK, score %.1f", self.id, self.properties['score'])
        
        return True
    
    def check_properties(self):
        """
        Validate properties received from HTTP status page
        """
        
        mandatory = [
            'id', 'os', 'soft', 'vers',
            'clients', 'clients_max',
            'total_bytes_in', 'total_bytes_out'
        ]
        
        for i in mandatory:
            if self.properties.get(i) == None:
                return self.error('Failed to get mandatory server property: "%s"' % i)
        
        if self.properties.get('id') != self.id:
            return self.error('Server ID mismatch: "%s" on server, "%s" expected' % (self.properties.get('id'), self.id))
        
        return True
    
    def poll_javaprssrvr3(self):
        """
        Poll javAPRSSrvr 3.x
        """
    
        # get front page, figure out which server type it is
        t_start = time.time()
        r = requests.get(self.status_url, headers=self.rhead, timeout=self.http_timeout)
        self.log.debug("%s: front %r", self.id, r.status_code)
        
        http_server = r.headers.get('server')
        if http_server != None:
            self.log.info("%s: Reports Server: %r - not javAPRSSrvr 3.x!", self.id, http_server)
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        
        if "javAPRSSrvr 3." not in d and "Pete Loveall AE5PL" not in d:
            self.log.info("%s: HTML does not mention javAPRSSrvr 3 or Pete", self.id)
            return False
            
        self.score.http_status_t = t_dur
        
        return self.parse_javaprssrvr3(d)
    
    def parse_javaprssrvr3(self, d):
        """
        Parse javAPRSSrvr 3.x HTML status page
        """
        
        self.log.debug("%s: parsing javAPRSSrvr 3.x HTML", self.id)
        
        # compiled regular expressions
        global javap3_re, javap3_re_num
        
        for k in javap3_re:
            reg = javap3_re[k]
            match = reg.search(d)
            if match == None:
                return self.error("javAPRSSrvr 3.x status page does not have '%s'" % k)
            self.properties[k] = match.group(1)
            #self.log.debug("%s: got %s: %s", self.id, k, self.properties[k])
        
        for k in javap3_re_num:
            reg = javap3_re_num[k]
            match = reg.search(d)
            if match == None:
                return self.error("javAPRSSrvr 3.x status page does not have numeric '%s'" % k)
            v = match.group(1)
            v = v.replace(',', '').replace('.', '') # has thousands separators: "78,527,080" *or* "78.527.080" !
            self.properties[k] = float(v)
            #self.log.debug("%s: got %s: %r", self.id, k, self.properties[k])
        
        self.properties['user_load'] = float(self.properties['clients']) / float(self.properties['clients_max']) * 100.0
        
        return True
    
    def poll_javaprssrvr4(self):
        """
        Get javAPRSSrvr 4 detail.xml
        """
        
        t_start = time.time()
        r = requests.get('%s%s' % (self.status_url, 'detail.xml'), headers=self.rhead, timeout=self.http_timeout)
        self.log.debug("%s: detail.xml %r", self.id, r.status_code)
        
        if r.status_code == 404:
            return None
            
        if r.status_code != 200:
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        self.score.http_status_t = t_dur
        
        return self.parse_javaprssrvr4(d)
    
    def parse_javaprssrvr4(self, d):
        """
        Parse javAPRSSrvr 4 detail.xml
        """
        
        try:
            # Python decodes the UTF-8 to an Unicode string, and etree does not
            # appreciate a pre-decoded Unicode string having an encoding parameter
            # in it. Work around: encode back to UTF-8.
            e = d.encode('utf-8')
            parser = etree.XMLParser(ns_clean=True, recover=True, encoding='utf-8')
            root = etree.fromstring(e, parser=parser)
        except Exception, exp:
            return self.error("detail.xml XML parsing failed: %s" % str(exp))
        
        if root == None or len(root) < 1:
            return self.error("detail.xml XML parsing failed (no elements)")
        
        if root.tag != 'javaprssrvr':
            return self.error("detail.xml: root tag is not javaprssrvr")
        
        #
        # app name/ver are in the software tag
        #
        
        sw_tag = root.find('software')
        if sw_tag == None:
            return self.error("detail.xml: No 'software' tag found")
        
        app_name = sw_tag.text
        app_ver = sw_tag.attrib.get('version')
        
        if app_name == None or app_ver == None:
            return self.error("detail.xml: Application name or version missing")
        
        self.properties['soft'] = app_name
        self.properties['vers'] = app_ver
        
        # server id
        dup_tag = root.find('dupeprocessor')
        if dup_tag == None:
            return self.error("detail.xml: No 'dupeprocessor' tag found")
        
        t = dup_tag.find('servercall')
        if t == None:
            return self.error("detail.xml: No 'servercall' tag found")
        
        self.properties['id'] = t.text
        
        #
        # operating system is in the java tag
        #
        
        java_tag = root.find('java')
        if java_tag == None:
            return self.error("detail.xml: No 'java' tag found")
        
        t = java_tag.find('os')
        if t == None:
            return self.error("detail.xml: No 'os' tag found")
        
        self.properties['os'] = "%s %s" % (t.text, t.attrib.get('architecture', ''))
        
        #
        # listener ports
        #
        
        listeners_tag = root.find('listenerports')
        if listeners_tag == None:
            return self.error("detail.xml: No 'listenerports' tag found")
        
        t = listeners_tag.find('connections')
        if t == None:
            return self.error("detail.xml: No 'connections' tag found for 'listenerports'")
        
        self.properties['clients'] = int(t.attrib.get('currentin'))
        self.properties['clients_max'] = int(t.attrib.get('maximum'))
        
        #
        # clients traffic
        #
        
        clients_tag = root.find('clients')
        if clients_tag == None:
            return self.error("detail.xml: No 'clients' tag")
        
        self.properties['total_bytes_in'] = int(clients_tag.find('rcvdtotals').attrib.get('bytes'))
        self.properties['total_bytes_out'] = int(clients_tag.find('xmtdtotals').attrib.get('bytes'))
        
        self.properties['user_load'] = float(self.properties['clients']) / float(self.properties['clients_max']) * 100.0
        
        return True
        
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        t_start = time.time()
        r = requests.get('%s%s' % (self.status_url, 'status.json'), headers=self.rhead, timeout=self.http_timeout)
        self.log.debug("%s: status.json %r", self.id, r.status_code)
        
        if r.status_code == 404:
            return None
            
        if r.status_code != 200:
            return False
        
        d = r.content
        t_end = time.time()
        t_dur = t_end - t_start
        self.score.http_status_t = t_dur
        
        try:
            j = json.loads(d)
        except Exception as e:
            self.log.info("%s: JSON parsing failed: %r", self.id, e)
            return self.error('aprsc status.json JSON parsing failed')
        
        return self.parse_aprsc(j)
        
    def parse_aprsc(self, j):
        """
        Parse aprsc status JSON
        """
        
        #
        # server block
        #
        j_server = j.get('server')
        if j_server == None:
            return self.error('aprsc status.json does not have a server block')
        
        server_keys = {
            'id': 'server_id',
            'soft': 'software',
            'vers': 'software_version',
            'os': 'os'
        }
        
        if not self.aprsc_get_keys(j_server, server_keys, 'server'):
            return False
        
        #
        # totals block
        #
        j_totals = j.get('totals')
        if j_totals == None:
            return self.error('aprsc status.json does not have a totals block')
        
        totals_keys = {
            'clients': 'clients',
            'clients_max': 'clients_max',
        }
        
        if not self.aprsc_get_keys(j_totals, totals_keys, 'totals'):
            return False
        
        self.properties['total_bytes_out'] = j_totals.get('tcp_bytes_tx', 0) + j_totals.get('udp_bytes_tx', 0) + j_totals.get('sctp_bytes_tx', 0)
        self.properties['total_bytes_in'] = j_totals.get('tcp_bytes_rx', 0) + j_totals.get('udp_bytes_rx', 0) + j_totals.get('sctp_bytes_rx', 0)
        
        # user load percentage
        worst_load = u_load = float(self.properties['clients']) / float(self.properties['clients_max']) * 100.0
        
        #
        # go through port listeners
        #
        j_listeners = j.get('listeners')
        if j_listeners == None:
            return self.error('aprsc status.json does not have a listeners block')
        
        for l in j_listeners:
            addr = l.get('addr')
            
            proto = l.get('proto')
            if proto == None:
                return self.error('aprsc status.json listener does not specify protocol')
            
            if proto == 'udp':
                continue
            
            u = l.get('clients')
            m = l.get('clients_max')
            if u == None or m == None:
                return self.error('aprsc status.json listener does not specify number of clients')
            l_load = float(u) / float(m) * 100.0
            
            #self.log.debug("%s: listener %r %d/%d load %.1f %%", self.id, addr, u, m, l_load)
            
            if l_load > worst_load:
                worst_load = l_load
        
        self.properties['user_load'] = u_load
        self.properties['worst_load'] = worst_load
        
        return True
        
    def aprsc_get_keys(self, src, keys, blockid):
        """
        Get a set of keys from aprsc json block, with error checking
        """
        for i in keys:
            k = keys[i]
            v = self.properties[i] = src.get(k)
            if v == None:
                return self.error('aprsc status.json block "%s" does not specify "%s"' % (blockid, k))
        
        return True

    def service_tests(self):
        """
        Perform APRS-IS service tests
        """
        
        t = aprsis.TCPPoll(self.log)
        ok = True
        ok_count = 0
        
        for ac in ('ip4', 'ip6'):
            if ac in self.server:
                t_start = time.time()
                r = t.poll(self.server[ac], 14580, self.id)
                t_dur = time.time() - t_start
                
                if r != True:
                    self.error("%s TCP 14580: %s" % (ac, r))
                    ok = False
                else:
                    ok_count += 1
                    self.score.poll_t_14580[ac] = t_dur
        
        return ok and ok_count > 0

