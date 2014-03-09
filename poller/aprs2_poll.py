
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
    'uptime': re.compile('<TD[^>]*>Total Up Time</TD><TD>([^>]+)</TD></TR>'),
}

javap3_re_num = {
    # depending on server's system locale these integers have thousands separators, or not, either '.' or ',', "'"
    'clients': re.compile('<TD[^>]*>Current Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'clients_max': re.compile('<TD[^>]*>Maximum Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'total_bytes_in': re.compile('<TD[^>]*>Total Bytes In</TD><TD>([\\d,\\.\']+)</TD>'),
    'total_bytes_out': re.compile('<TD[^>]*>Total Bytes Out</TD><TD>([\\d,\\.\']+)</TD>'),
}

javap3_re_uptime = re.compile('(\\d+)(\\.\d+){0,1}([dhms])(.*)')

class Poll:
    def __init__(self, log, server, software_type_cache, rates_cache):
        self.log = log
        self.server = server
        self.software_type_cache = software_type_cache
        self.rates_cache = rates_cache
        self.id = server['id']
        self.status_url = 'http://%s:14501/' % self.server['ipv4']
        self.rhead = {'User-agent': 'aprs2net-poller/2.0'}
        self.http_timeout = 5.0
        self.http_got_t = None
        self.client_cap = 300
        
        self.try_order = ['javap3', 'aprsc', 'javap4']
        
        self.properties = {}
        
        self.score = aprs2_score.Score()
        
        self.errors = []
    
    def error(self, code, msg):
        """
        Push an error to the list of errors
        """
        
        self.log.info("%s: Polling error [%s]: %s", self.id, code, msg)
        self.errors.append([code, msg])
        return False
    
    def poll(self):
        """
        Run a polling round.
        """
        self.log.info("polling %s", self.id)
        self.log.debug("config: %r", self.server)
        
        # check if we know its software type already
        try_first = self.software_type_cache.get(self.id)
        if try_first != None:
            if try_first not in self.try_order:
                self.log.info("%s: software type cache says '%s' which we don't know about", self.id, try_first)
                del self.software_type_cache[self.id]
            else:
                self.try_order.remove(try_first)
                self.try_order.insert(0, try_first)
        
        ok = False
        for t in self.try_order:
            r = False
            
            if t == 'aprsc':
                r = self.poll_aprsc()
            if t == 'javap4':
                r = self.poll_javaprssrvr4()
            if t == 'javap3':
                r = self.poll_javaprssrvr3()
            
            # Not this type, but might be alive?
            if r == None:
                continue
            
            # Is broken?
            if r == False:
                return False
            
            self.log.debug("%s: HTTP %s OK %.3f s", self.id, t, self.score.http_status_t)
            
            if self.check_properties() == False:
                return False
                
            self.calculate_rates()
            
            self.log.debug("%s: Server users %d/%d (%.1f %% total, %.1f %% worst-case)",
                self.id, self.properties['clients'], self.properties['clients_max'], self.properties['user_load'], self.properties['worst_load'])
            
            self.software_type_cache[self.id] = t
            
            # Works, great!
            ok = True
            break
        
        if ok == False:
            self.error('web-undetermined', "Server status not determined: %r" % self.id)
            return False
        
        # Test that the required APRS-IS services are working
        if not self.service_tests():
            return False
        
        self.properties['score'] = self.score.get(self.properties)
        self.properties['scorebase'] = self.score.score_components
        self.log.info("%s: Server OK, score %.1f: %r", self.id, self.properties['score'], self.score.score_components)
        
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
                return self.error('web-props', 'Failed to get mandatory server property: "%s"' % i)
        
        if self.properties.get('id') != self.id:
            return self.error('id-mismatch', 'Server ID mismatch: "%s" on server, "%s" expected' % (self.properties.get('id'), self.id))
        
        return True
    
    def calculate_rates(self):
        """
        Calculate bytes/sec rates
        """
        
        now = time.time()
        rkeys = {
            'total_bytes_in': 'rate_bytes_in',
            'total_bytes_out': 'rate_bytes_out',
        }
        
        prev = self.rates_cache.get(self.id)
        if prev:
            dur_t = now - prev['t']
            for i in rkeys.keys():
                if self.properties[i] > prev[i]:
                    self.properties[rkeys[i]] = (self.properties[i] - prev[i]) / dur_t
        
        prev = {
            't' : now
        }
        for i in rkeys.keys():
            prev[i] = self.properties[i]
        self.rates_cache[self.id] = prev
    
    def javap3_decode_uptime(self, s):
    	"""
    	Decode javaprssrvr3 uptime string
    	"""
    	self.log.debug("javap3 uptime: %s", s)
    	# 132d18h34m27.215s
    	
        mul = {
            'd': 86400,
            'h': 3600,
            'm': 60,
            's': 1
        }
        
        up = 0
    	while True and s != '':
    	    match = javap3_re_uptime.match(s)
    	    if match == None:
    	        break;
    	    #self.log.debug("  found: %s %s", match.group(1), match.group(3))
    	    
    	    m = mul.get(match.group(3))
    	    if m != None:
    	        up += int(match.group(1)) * m	
    	    
    	    s = match.group(4)
    	    #self.log.debug("  left: %s", s)
    	
    	return up
    
    def poll_javaprssrvr3(self):
        """
        Poll javAPRSSrvr 3.x
        """
    
        # get front page, figure out which server type it is
        t_start = time.time()
        try:
            r = requests.get(self.status_url, headers=self.rhead, timeout=self.http_timeout)
            d = r.content
        except Exception as e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /: Connection error: %s" % (self.id, str(e)))
            
        t_end = time.time()
        t_dur = t_end - t_start
        
        self.log.debug("%s: HTTP GET / returned: %r", self.id, r.status_code)
        
        http_server = r.headers.get('server')
        if http_server != None:
            self.log.info("%s: Reports Server: %r - not javAPRSSrvr 3.x", self.id, http_server)
            return None
        
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
                return self.error('web-parse-fail', "javAPRSSrvr 3.x status page does not have '%s'" % k)
            self.properties[k] = match.group(1)
            #self.log.debug("%s: got %s: %s", self.id, k, self.properties[k])
        
        for k in javap3_re_num:
            reg = javap3_re_num[k]
            match = reg.search(d)
            if match == None:
                return self.error('web-parse-fail', "javAPRSSrvr 3.x status page does not have numeric '%s'" % k)
            v = match.group(1)
            # javaprssrvr uses thousands separators based on current locale at server:
            # "78,527,080" *or* "78.527.080" or "78'527'080" !
            v = v.replace(',', '').replace('.', '').replace("'", '')
            self.properties[k] = float(v)
            #self.log.debug("%s: got %s: %r", self.id, k, self.properties[k])
        
        self.properties['uptime'] = self.javap3_decode_uptime(self.properties['uptime'])
        self.properties['user_load'] = float(self.properties['clients']) / float(min(self.client_cap, self.properties['clients_max'])) * 100.0
        self.properties['worst_load'] = self.properties['user_load']
        self.properties['type'] = 'javap3'
        
        return True
    
    def poll_javaprssrvr4(self):
        """
        Get javAPRSSrvr 4 detail.xml
        """
        
        t_start = time.time()
        try:
            r = requests.get('%s%s' % (self.status_url, 'detail.xml'), headers=self.rhead, timeout=self.http_timeout)
            d = r.content
        except Exception as e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /detail.xml: Connection error: %s" % (self.id, str(e)))
            
        if r.status_code == 404:
            self.log.info("%s: detail.xml 404 Not Found - not javAPRSSrvr 4", self.id)
            return None
            
        self.log.debug("%s: HTTP GET /detail.xml returned: %r", self.id, r.status_code)
        
        if r.status_code != 200:
            return False
        
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
            return self.error('web-xml-fail', "detail.xml XML parsing failed: %s" % str(exp))
        
        if root == None or len(root) < 1:
            return self.error('web-xml-fail', "detail.xml XML parsing failed (no elements)")
        
        if root.tag != 'javaprssrvr':
            return self.error('web-parse-fail', "detail.xml: root tag is not javaprssrvr")
        
        #
        # app name/ver are in the software tag
        #
        
        sw_tag = root.find('software')
        if sw_tag == None:
            return self.error('web-parse-fail', "detail.xml: No 'software' tag found")
        
        app_name = sw_tag.text
        app_ver = sw_tag.attrib.get('version')
        
        if app_name == None or app_ver == None:
            return self.error('web-parse-fail', "detail.xml: Application name or version missing")
        
        self.properties['soft'] = app_name
        self.properties['vers'] = app_ver
        self.properties['type'] = 'javap4'
        
        # server id
        dup_tag = root.find('dupeprocessor')
        if dup_tag == None:
            return self.error('web-parse-fail', "detail.xml: No 'dupeprocessor' tag found")
        
        t = dup_tag.find('servercall')
        if t == None:
            return self.error('web-parse-fail', "detail.xml: No 'servercall' tag found")
        
        self.properties['id'] = t.text
        
        #
        # operating system is in the java tag
        #
        
        java_tag = root.find('java')
        if java_tag == None:
            return self.error('web-parse-fail', "detail.xml: No 'java' tag found")
        
        t = java_tag.find('os')
        if t == None:
            return self.error('web-parse-fail', "detail.xml: No 'os' tag found")
        
        self.properties['os'] = "%s %s" % (t.text, t.attrib.get('architecture', ''))
        
        time_tag = java_tag.find('time')
        if time_tag == None:
            return self.error('web-parse-fail', "detail.xml: No 'time' tag found")
            
        t = time_tag.find('up')
        if t == None:
            return self.error('web-parse-fail', "detail.xml: No 'up' uptime tag found")
        
        self.properties['uptime'] = int(t.attrib.get('millis', 0)) / 1000
        
        #
        # listener ports
        #
        
        listeners_tag = root.find('listenerports')
        if listeners_tag == None:
            return self.error('web-parse-fail',"detail.xml: No 'listenerports' tag found")
        
        t = listeners_tag.find('connections')
        if t == None:
            return self.error('web-parse-fail', "detail.xml: No 'connections' tag found for 'listenerports'")
        
        self.properties['clients'] = int(t.attrib.get('currentin'))
        self.properties['clients_max'] = int(t.attrib.get('maximum'))
        
        #
        # clients traffic
        #
        
        clients_tag = root.find('clients')
        if clients_tag == None:
            return self.error('web-parse-fail', "detail.xml: No 'clients' tag")
        
        self.properties['total_bytes_in'] = int(clients_tag.find('rcvdtotals').attrib.get('bytes'))
        self.properties['total_bytes_out'] = int(clients_tag.find('xmtdtotals').attrib.get('bytes'))
        
        self.properties['user_load'] = float(self.properties['clients']) / float(min(self.client_cap, self.properties['clients_max'])) * 100.0
        self.properties['worst_load'] = self.properties['user_load']
        
        return True
        
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        t_start = time.time()
        try:
            r = requests.get('%s%s' % (self.status_url, 'status.json'), headers=self.rhead, timeout=self.http_timeout)
            d = r.content
        except Exception as e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /status.json: Connection error: %s" % (self.id, str(e)))
            
        self.log.debug("%s: HTTP GET /status.json returned: %r", self.id, r.status_code)
        
        if r.status_code == 404:
            return None
            
        if r.status_code != 200:
            return False
        
        t_end = time.time()
        t_dur = t_end - t_start
        self.score.http_status_t = t_dur
        
        try:
            j = json.loads(d)
        except Exception as e:
            self.log.info("%s: JSON parsing failed: %r", self.id, e)
            return self.error('web-json-fail', 'aprsc status.json JSON parsing failed')
        
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
            return self.error('web-parse-fail', 'aprsc status.json does not have a server block')
        
        server_keys = {
            'id': 'server_id',
            'soft': 'software',
            'vers': 'software_version',
            'os': 'os',
            'uptime': 'uptime'
        }
        
        if not self.aprsc_get_keys(j_server, server_keys, 'server'):
            return False
        
        self.properties['type'] = 'aprsc'
        
        #
        # totals block
        #
        j_totals = j.get('totals')
        if j_totals == None:
            return self.error('web-parse-fail', 'aprsc status.json does not have a totals block')
        
        totals_keys = {
            'clients': 'clients',
            'clients_max': 'clients_max',
        }
        
        if not self.aprsc_get_keys(j_totals, totals_keys, 'totals'):
            return False
        
        self.properties['total_bytes_out'] = j_totals.get('tcp_bytes_tx', 0) + j_totals.get('udp_bytes_tx', 0) + j_totals.get('sctp_bytes_tx', 0)
        self.properties['total_bytes_in'] = j_totals.get('tcp_bytes_rx', 0) + j_totals.get('udp_bytes_rx', 0) + j_totals.get('sctp_bytes_rx', 0)
        
        # user load percentage
        worst_load = u_load = float(self.properties['clients']) / float(min(self.client_cap, self.properties['clients_max'])) * 100.0
        
        #
        # go through port listeners
        #
        j_listeners = j.get('listeners')
        if j_listeners == None:
            return self.error('web-parse-fail', 'aprsc status.json does not have a listeners block')
        
        for l in j_listeners:
            addr = l.get('addr')
            
            proto = l.get('proto')
            if proto == None:
                return self.error('web-parse-fail', 'aprsc status.json listener does not specify protocol')
            
            if proto == 'udp':
                continue
            
            u = l.get('clients')
            m = l.get('clients_max')
            if u == None or m == None:
                return self.error('web-parse-fail', 'aprsc status.json listener does not specify number of clients')
            l_load = float(u) / float(min(self.client_cap, m)) * 100.0
            
            self.log.debug("%s: listener %r %d/%d load %.1f %%", self.id, addr, u, m, l_load)
            
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
                return self.error('web-parse-fail', 'aprsc status.json block "%s" does not specify "%s"' % (blockid, k))
        
        return True

    def poll_http_submit(self):
        """
        Poll the HTTP submission port 8080
        """
        
        # This is quite silly.
        # We have to test that port 8080 actually responds in a way that indicates that
        # it's a supported server which would accept position posts.
        # But the servers don't tend to return sensible return codes unless we actually
        # transmit a packet, and we don't want to do that. So we just do a GET and see that
        # we get the expected error code. None of the servers return a Server: header
        # in this case.
        retcodes = {
            'aprsc': 501, # Not implemented
            'javap3': 400, # Bad request
            'javap4': 405 # Method not allowed
        }
        
        # For some reason python-requests does not accept IPv6 literal addresses in an URL.
        # So, let's go IPv4 only for now.
        for ac in ('ipv4',):
            if ac in self.server:
                if ac == 'ipv4':
                    url = 'http://%s:8080/' % self.server[ac]
                else:
                    url = 'http://[%s]:8080/' % self.server[ac]
                    
                t_start = time.time()
                try:
                    r = requests.get(url, headers=self.rhead, timeout=self.http_timeout)
                except Exception as e:
                    self.log.info("%s: HTTP submit 8080: Connection error: %r", self.id, e)
                    continue
                    
                t_dur = time.time() - t_start
                
                http_server = r.headers.get('server')
                if http_server != None:
                    self.log.info("%s: HTTP submit 8080: Reports Server: %r - not a HTTP submit port!", self.id, http_server)
                    continue
                
                expect_code = retcodes.get(self.properties['type'])
                if r.status_code != expect_code:
                    self.log.info("%s: HTTP submit 8080: return code %d != expected %r - not a HTTP submit port!", self.id, r.status_code, expect_code)
                    continue
                
                self.log.info("%s: HTTP submit 8080: return code %r - OK, looks like a submit port (%.3f s)", self.id, r.status_code, t_dur)
                self.properties['submit-http-8080-' + ac] = t_dur
                
    
    def service_tests(self):
        """
        Perform APRS-IS service tests
        """
        
        self.poll_http_submit()
        
        t = aprsis.TCPPoll(self.log)
        ok = True
        ok_count = 0
        
        port = 14580
        if self.id.startswith('T2HUB'):
            port = 20152
        
        for ac, prefix in (('ipv4', 'IS4'), ('ipv6', 'IS6')):
            if self.server.get(ac) != None:
                t_start = time.time()
                [code, msg] = t.poll(self.server[ac], port, self.id, prefix)
                t_dur = time.time() - t_start
                
                if code != 'ok':
                    self.error(code, "%s TCP %d: %s" % (ac, port, msg))
                    ok = False
                else:
                    ok_count += 1
                    self.score.poll_t_14580[ac] = t_dur
        
        return ok and ok_count > 0

