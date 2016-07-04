
import time
import requests
import json
import re
import socket
from lxml import etree

import aprsis
import aprs2_score
import aprs2_redis

# compile regular expressions to make them run faster
javap3_re = {
    'id': re.compile('<TD[^>]*>Server ID</TD><TD>([^>]+)</TD>'),
    'os': re.compile('<TD[^>]*>OS</TD><TD>([^>]+)</TD>'),
    'soft': re.compile('<TH[^>]*>(javAPRSSrvr) \\d+.\\d+[^>]+<BR>'),
    'vers': re.compile('<TH[^>]*>javAPRSSrvr (\\d+.\\d+[^>]+)<BR>'),
    'uptime': re.compile('<TD[^>]*>Total Up Time</TD><TD>([^>]+)</TD></TR>'),
}

javap3_re_num = {
    # depending on server's system locale these integers have thousands separators, or not, either '.' or ',', "'", " "
    'clients': re.compile('<TD[^>]*>Current Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'clients_max': re.compile('<TD[^>]*>Maximum Inbound Connections</TD><TD>([\\d,\\.]+)</TD>'),
    'connects': re.compile('<TD[^>]*>Total Inbound Connects</TD><TD>([\\d,\\.]+)</TD>'),
    'total_bytes_in': re.compile('<TD[^>]*>Total Bytes In</TD><TD>([^<]+)</TD>'),
    'total_bytes_out': re.compile('<TD[^>]*>Total Bytes Out</TD><TD>([^<]+)</TD>'),
}

javap3_re_outbound = re.compile('<TH[^>]*>Outbound Connections</TH>.*?<TR[^>]*>.*?</TR>(.*?)</TBODY>', re.DOTALL)
# <TR align=right><TD align=middle><A href="http://193.190.240.226:14501">hub1.aprs2.net/193.190.240.226:20152</A></TD>
# <TD align=middle>C1BEF0E2</TD>
# <TD align=middle>Yes</TD>
# <TD align=middle>aprsc 2.0.11&#8209;g6099cb1</TD>
# <TD>5d14h00m45.881s</TD> (connected uplink)
# <TD>21,334,472</TD> (Packets Rcvd)
# <TD>498,551</TD> (Packets Sent)
# <TD>1,937,147,236</TD> (Bytes Rcvd)
# <TD>44,844,765</TD> (Bytes Sent)
# <TD>32,122</TD> (Rcv bps)
# <TD>743</TD> (Send bps)
# <TD>00.025s</TD> (Last packet in)
# <TD>4,048</TD> (Looped)
# <TD>0</TD></TR> (Queue depth (ms))
javap3_re_outbound_line = re.compile('<TR[^>]*><TD[^>]*><A[^>]+>([^/<]+)/([^<]+)</A></TD><TD[^>]*>(.*?)</TD><TD[^>]*>(.*?)</TD><TD[^>]*>(.*?)</TD><TD[^>]*>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD><TD>(.*?)</TD>(.*)')
javap3_re_uptime = re.compile('(\\d+)(\\.\d+){0,1}([dhms])(.*)')
javap3_re_numeric_sanitize = re.compile('[^\\d]+')

re_ipv4_port = re.compile('(\\d+\\.\\d+\\.\\d+\\.\\d+):(\\d+)')
re_ipv6_port = re.compile('([0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+:[0-9a-f]+):(\\d+)')

def javap3_strfloat(s):
    # replace non-digits with empty strings
    s = javap3_re_numeric_sanitize.sub('', s)
    return float(s)

def inet6_normalize(addr_s):
    try:
        internal = socket.inet_pton(socket.AF_INET6, addr_s)
        return socket.inet_ntop(socket.AF_INET6, internal)
    except socket.error:
        return None

class Poll:
    def __init__(self, log, server, red, software_type_cache, rates_cache, address_map):
        self.log = log
        self.server = server
        self.red = red
        self.software_type_cache = software_type_cache
        self.rates_cache = rates_cache
        self.address_map = address_map
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
    
    def map_addr_id(self, addr):
        """
        Map server address to a server ID, if possible (for figuring out uplink)
        """
        
        self.log.debug("mapping address to server: %r", addr)
        
        v4 = re_ipv4_port.match(addr)
        if v4 != None:
            return self.address_map.get(v4.group(1))
        
        v6 = re_ipv6_port.match(addr)
        if v6 != None:
            #self.log.debug("  v6 addr: %r", inet6_normalize(v6.group(1)))
            return self.address_map.get(inet6_normalize(v6.group(1)))
        
        return "unknown"
    
    def poll_main(self):
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
        
        if not self.check_uplink():
            return False
        
        return True
    
    def poll(self):
        success = self.poll_main()
        
        if success != True:
            self.score.score_add('server-fail', 1000, '1000')
            
        self.properties['score'] = self.score.get(self.properties)
        self.properties['scorebase'] = self.score.score_components
        self.log.info("%s: Server %s, score %.1f: %r", 'OK' if success else 'FAIL', self.id, self.properties['score'], self.score.score_components)
        
        return success
    
    def check_properties(self):
        """
        Validate properties received from HTTP status page
        """
        
        mandatory = [
            'id', 'os', 'soft', 'vers',
            'clients', 'clients_max', 'connects',
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
            'connects': 'rate_connects',
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
        except Exception, e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /: Connection error: %s" % (self.id, e))
            
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
            try:
                self.properties[k] = javap3_strfloat(v)
            except Exception:
                return self.error('web-parse-fail', "javAPRSSrvr 3.x status page, numeric '%s' parsing failed" % k)
            #self.log.debug("%s: got %s: %r", self.id, k, self.properties[k])
        
        self.properties['uptime'] = self.javap3_decode_uptime(self.properties['uptime'])
        self.properties['user_load'] = float(self.properties['clients']) / float(min(self.client_cap, self.properties['clients_max'])) * 100.0
        self.properties['worst_load'] = self.properties['user_load']
        self.properties['type'] = 'javap3'
        
        #
        # uplinks
        #
        
        ups = javap3_re_outbound.search(d)
        if ups != None:
            #self.log.debug("found outbound: %r", ups.group(1))
            s = ups.group(1)
            upl = []
            
            while s:
                m = javap3_re_outbound_line.search(s)
                if not m:
                    break
                hname = m.group(1)
                haddr = m.group(2)
                uptime = self.javap3_decode_uptime(m.group(6))
                rx_packets = javap3_strfloat(m.group(7))
                rx_last = self.javap3_decode_uptime(m.group(13))
                id = self.map_addr_id(haddr)
                self.log.debug("   server: host %s addr %s up %r rx_packets %s rx_last %s id %r", hname, haddr, uptime, rx_packets, rx_last, id)
                s = m.group(14)
                self.log.debug("   left: %s", s)
                upl.append({
                    'id': id,
                    'addr_rem': haddr,
                    'up': uptime,
                    'rx_packets': rx_packets,
                    'rx_last': rx_last
                })
            
            self.properties['uplinks'] = upl
        
        return True
    
    def poll_javaprssrvr4(self):
        """
        Get javAPRSSrvr 4 detail.xml
        """
        
        t_start = time.time()
        try:
            r = requests.get('%s%s' % (self.status_url, 'detail.xml'), headers=self.rhead, timeout=self.http_timeout)
            d = r.content
        except Exception, e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /detail.xml: Connection error: %s" % (self.id, e))
            
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
            parser = etree.XMLParser(ns_clean=True, recover=True)
            root = etree.fromstring(d, parser=parser)
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
            
        self.properties['connects'] = int(clients_tag.attrib.get('total'))
        
        self.properties['total_bytes_in'] = int(clients_tag.find('rcvdtotals').attrib.get('bytes'))
        self.properties['total_bytes_out'] = int(clients_tag.find('xmtdtotals').attrib.get('bytes'))
        
        self.properties['user_load'] = float(self.properties['clients']) / float(min(self.client_cap, self.properties['clients_max'])) * 100.0
        self.properties['worst_load'] = self.properties['user_load']
        
        #
        # uplinks
        #
        
        clientrcv = clients_tag.findall('clientrcv')
        if clientrcv != None:
            upl = []
            
            # current time at the server (sometimes wildly off from real time, when no NTP in use)
            currtime = time_tag.find('current')
            if currtime == None:
                return self.error('web-parse-fail', "detail.xml: No 'current' time tag found")
            currtime = float(currtime.attrib.get("utc"))
            
            for cl in clientrcv:
                #self.log.debug(" client")
                
                logi = cl.find("login")
                if logi == None:
                    continue
                    
                tm = cl.find("time")
                if tm == None:
                    continue
                    
                callssid = logi.find("callssid")
                up = cl.find("upstream")
                rcv = cl.find("rcvdfrom")
                rem = cl.find("remoteserver")
                ctime = tm.find("connect")
                lastlinein = tm.find("lastlinein")
                
                if callssid == None or up == None or up.text != "true" or ctime == None:
                    continue
                
                client_class_tag = cl.find("class")
                if client_class_tag == None:
                    continue
                
                client_class = client_class_tag.attrib.get("name")
                if client_class != "UpstreamClientRcv":
                    continue
                
                # uplink connection uptime, convert to seconds
                ctime = float(ctime.attrib.get("utc"))
                uptime = (currtime - ctime) / 1000
                
                # when data was last received from connection, convert to seconds
                lastlinein = float(lastlinein.attrib.get("utc"))
                lastlinein = (currtime - lastlinein) / 1000
                
                self.log.debug(" upstream client %s class %s", callssid.text, client_class)
                
                rem = "%s:%s" % (rem.text, rem.attrib.get('port', ''))
                
                upl.append({
                    'id': callssid.text,
                    'addr_rem': rem,
                    'up': int(uptime),
                    'rx_last': lastlinein,
                    'rx_packets': int(rcv.attrib.get('packets', '0')),
                })
                
            self.properties['uplinks'] = upl
                
        
        return True
        
    def poll_aprsc(self):
        """
        Get aprsc's status.json
        """
        
        t_start = time.time()
        try:
            r = requests.get('%s%s' % (self.status_url, 'status.json'), headers=self.rhead, timeout=self.http_timeout)
            d = r.content
        except Exception, e:
            return self.error('web-http-fail', "%s: HTTP status page 14501 /status.json: Connection error: %s" % (self.id, e))
            
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
            'connects': 'connects'
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
        
        #
        # uplinks
        #
        j_uplinks = j.get('uplinks')
        if j_uplinks != None:
            upl = []
            for u in j_uplinks:
                upl.append({
                    'id': u.get('username'),
                    'addr_rem': u.get('addr_rem'),
                    'up': u.get('since_connect'),
                    'rx_last': u.get('since_last_read'),
                    'rx_packets': u.get('pkts_rx'),
                })
            self.properties['uplinks'] = upl
        
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
                except Exception, e:
                    self.log.info("%s: HTTP submit 8080: Connection error: %s", self.id, e)
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
    
    def check_uplink(self):
        """
        Check that a server's uplink status is acceptable
        """
        
        uplinks_required = True
        required_upstream = None
        member_of = self.server.get('member', [])
        
        if 'firenet.aprs2.net' in member_of:
            self.log.debug("member of firenet.aprs2.net, not tracking uplinks")
            return True
            
        if 'rotate.aprs2.net' in member_of:
            self.log.debug("member of rotate.aprs2.net")
            required_upstream = 'hubs.aprs2.net'
            
        if 'hubs.aprs2.net' in member_of:
            self.log.debug("member of hubs.aprs2.net")
            required_upstream = 'rotate.aprs.net'
            
        if 'rotate.aprs.net' in member_of or 'cwop.aprs.net' in member_of:
            self.log.debug("member of core or cwop, no need for uplinks")
            uplinks_required = False
            
        ups = self.properties.get('uplinks', [])
        
        self.log.debug("uplinks: %r", ups)
        
        if uplinks_required == False:
            if len(ups) == 0:
                return True
                
            return self.error('uplinks-has', 'Server is linked to upstream servers - not expected for this server class')
        
        if len(ups) < 1:
            return self.error('uplinks-none', 'Not connected to an upstream server')
        
        if len(ups) > 1:
            return self.error('uplinks-many', 'Connected to more than 1 upstream server')
        
        upl = ups[0]
        
        uplink_server = self.red.getServer(upl.get('id'))
        self.log.debug("uplink is: %r", uplink_server)
        if uplink_server == None:
            return self.error('uplinks-odd', 'Connected to unregistered upstream server')
        
        uplink_member = uplink_server.get('member', [])
        if required_upstream and required_upstream not in uplink_member:
            return self.error('uplinks-wrong', 'Connected to wrong upstream server')
        
        if upl.get('rx_last') > 300:
            return self.error('uplinks-stuck', 'Uplink stuck: last received data %d seconds ago' % upl.get('rx_last'))
        
        self.log.info('Uplink: Connected to %s [%s]', upl.get('addr_rem'), upl.get('id'))
        
        return True
    
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

