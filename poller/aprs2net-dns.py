#!/usr/bin/python

import time
import logging
import logging.config
import ConfigParser
import sys
import traceback
import types
import socket
from urlparse import urlparse

import requests
import json

import aprs2_redis
import aprs2_config

# dnspython.org
import dns.query
import dns.tsigkeyring
import dns.update

# All configuration variables need to be strings originally.
CONFIG_SECTION = 'dns'
DEFAULT_CONF = {
    # Server polling interval
    'poll_interval': '120',
    
    'max_test_result_age': '660',
    'min_polled_servers': '80',
    'min_polled_ok_pct': '55',
    
    # Portal URL for downloading configs
    'portal_servers_url': 'https://portal-url.example.com/blah',
    'portal_rotates_url': 'https://portal-url.example.com/blah',
    
    # rotates which are not managed
    'unmanaged_rotates': 'hubs.aprs2.net hub-rotate.aprs2.net',
    
    # DNS TTL
    'dns_ttl': '600',
}

class DNSDriver:
    """
    aprs2.net DNS driver
    """
    def __init__(self, config_file='poller.conf'):
        # read logging config file
        logging.config.fileConfig(config_file)
        logging.Formatter.converter = time.gmtime
        
        self.log = logging.getLogger('dns')
        self.log.info("Starting up")
        
        # read configuration
        self.config = ConfigParser.ConfigParser()
        self.config.add_section(CONFIG_SECTION)
        
        for option, value in DEFAULT_CONF.iteritems():
            self.config.set(CONFIG_SECTION, option, value)
            
        self.config.read(config_file)
        
        self.dns_master = self.config.get(CONFIG_SECTION, 'dns_master')
        self.poll_interval = self.config.getint(CONFIG_SECTION, 'poll_interval')
        self.dns_zones = self.config.get(CONFIG_SECTION, 'dns_zones').split(' ')
        self.master_rotate = self.config.get(CONFIG_SECTION, 'master_rotate')
        self.unmanaged_rotates = self.config.get(CONFIG_SECTION, 'unmanaged_rotates').split(' ')
        self.pollers = self.config.get(CONFIG_SECTION, 'pollers').split(' ')
        self.max_test_result_age = self.config.getint(CONFIG_SECTION, 'max_test_result_age')
        self.min_polled_servers = self.config.getint(CONFIG_SECTION, 'min_polled_servers')
        self.min_polled_ok_pct = self.config.getint(CONFIG_SECTION, 'min_polled_ok_pct')
        
        self.dns_keyring = dns.tsigkeyring.from_text({ 'aprs2net-dns.' : self.config.get(CONFIG_SECTION, 'dns_tsig_key') })
        self.dns_ttl = self.config.getint(CONFIG_SECTION, 'dns_ttl')

        self.rhead = {'User-agent': 'aprs2net-dns/2.0'}
        self.http_timeout = 10.0
        
        # cache DNS state for each name, to prevent updates which do not change anything
        self.dns_update_cache = {}
        
        # config object for the web UI
        self.web_config = {
            'site_descr': self.config.get(CONFIG_SECTION, 'site_descr'),
            'master': 1
        }
        
        # redis client
        self.red = aprs2_redis.APRS2Redis(db=1)
        self.red.setWebConfig(self.web_config)
        self.config_manager = aprs2_config.ConfigManager(logging.getLogger('config'),
        	self.red,
        	self.config.get(CONFIG_SECTION, 'portal_servers_url'),
        	self.config.get(CONFIG_SECTION, 'portal_rotates_url'))
    
    def fetch_full_status(self):
        """
        Fetch full status from each of the masters
        """
        
        status_set = {}
        
        for url in self.pollers:
            self.log.info("Fetching status: %s", url)
            siteid = urlparse(url).netloc
            
            t_start = time.time()
            
            try:
                r = requests.get('%sapi/full' % url, headers=self.rhead, timeout=self.http_timeout)
                d = r.content
            except Exception as e:
                self.log.error("%s: HTTP full JSON status fetch: Connection error: %s", siteid, str(e))
                continue
            
            if r.status_code != 200:
                self.log.error("%s: HTTP full JSON status fetch: Status code %d", siteid, r.status_code)
                continue
            
            t_end = time.time()
            t_dur = t_end - t_start
            
            self.log.debug("%s: HTTP GET /api/full returned: %r (%.3f s)", siteid, r.status_code, t_dur)
            
            try:
                j = json.loads(d)
            except Exception as e:
                self.log.error("%s: JSON parsing failed: %r", url, e)
                continue
            
            self.check_returned_status(siteid, j, status_set)
        
        return status_set
    
    def check_returned_status(self, siteid, j, status_set):
        """
        Check if the returned full status set is any good
        """
        
        if j.get('result') != 'full' and j.get('result') != 'ok':
            self.log.error("%s: Full status JSON does not have result: ok/full", siteid)
            return
        
        servers = j.get('servers')
        if not servers:
            self.log.error("%s: Full status JSON does not contain servers", siteid)
            return
        
        if not type(servers) is list:
            self.log.error("%s: Full status JSON: servers is not a list", siteid)
            return
        
        if len(servers) < self.min_polled_servers:
            self.log.error("%s: %d servers polled - too few (min %d)!", siteid, len(servers), self.min_polled_servers)
            return
        
        # Check that a good amount of servers in the set are OK,
        # discard the whole set if the poller itself is in trouble.
        servers_ok = [s for s in servers if s.get('status') and s.get('status').get('status') == 'ok']
        servers_ok_pct = 100.0 * len(servers_ok) / len(servers)
        self.log.info("%s: %d/%d (%.1f %%) servers OK", siteid, len(servers_ok), len(servers), servers_ok_pct)
        
        if servers_ok_pct < self.min_polled_ok_pct:
            self.log.error("%s: Too few servers OK (%d/%d: %.1f %% < %.0f %%) - poller having trouble?",
                siteid, len(servers_ok), len(servers), servers_ok_pct, self.min_polled_ok_pct)
            return
        
        for s in servers:
            self.add_returned_server(siteid, s, status_set)
    
    def add_returned_server(self, siteid, s, status_set):
        """
        Add a single returned server to current status set
        """
        
        cfg = s.get('config')
        stat = s.get('status')
        
        if cfg == None or stat == None:
            self.log.error("%s: Server in set, with config or status missing", siteid)
            return
        
        id = cfg.get('id')
        last_test = stat.get('last_test')
        
        if id == None or last_test == None:
            self.log.error("%s: Server in set, with id or last_test missing", siteid)
            return
        
        # the testing result must be quite recent to be useful
        test_age = time.time() - last_test
        if test_age > self.max_test_result_age:
            self.log.error("%s: [%s] test age %.0f > %.0f", siteid, id, test_age, self.max_test_result_age)
            return
        
        if id in status_set:
            set = status_set[id]
        else:
            set = status_set[id] = {}
        
        set[siteid] = stat
    
    def merge_status(self, servers, status_set):
        """
        Merge status sets to produce final score for each server
        """
        
        merged = {}
        
        for id in status_set:
            ok_count = 0
            scores = []
            errors = {}
            score_sum = 0.0
            latest_ok = None
            latest_fail = None
            latest = None
            
            merged_scorebase = {}
            avg_scorebase = {}
            
            for site in status_set[id]:
                stat = status_set[id][site]
                
                self.log.debug("status for %s at %s: %r", id, site, stat)
                
                status = stat.get('status', 'Unknown')
                if latest == None or latest.get('last_test', 0) < stat.get('last_test', 0):
                    latest = stat
                    
                if status == 'ok':
                    ok_count += 1
                    if latest_ok == None or latest_ok.get('last_test', 0) < stat.get('last_test', 0):
                        latest_ok = stat
                else:
                    score_sum += 1000.0
                    if latest_fail == None or latest_fail.get('last_test', 0) < stat.get('last_test', 0):
                        latest_fail = stat
                
                props = stat.get('props', {})
                
                if props != None and 'score' in props:
                    scores.append(props['score'])
                    score_sum += props['score']
                    if 'scorebase' in props:
                    	sb = props['scorebase']
                    	for k in sb:
                    	    avg_scorebase[k] = avg_scorebase[k] + sb[k] if (k in avg_scorebase) else sb[k]
                        merged_scorebase[site] = props['scorebase']
                
                e = stat.get('errors', [])
                for k, v in e:
                    errors[k] = v
            
            if ok_count >= 1 and float(ok_count) / len(status_set[id]) > 0.48:
                status = 'ok'
            else:
                status = 'fail'
            
            merged[id] = m = {
                'status': status,
                'c': '%d/%d' % (ok_count, len(status_set[id])),
                'c_ok': ok_count,
                'c_res': len(status_set[id])
            }
            
            if merged_scorebase:
            	m['merged_scorebase'] = merged_scorebase
            
            if latest:
                m['props'] = latest.get('props')
                m['last_test'] = latest.get('last_test')
            
            if errors:
                m['errors'] = [[k, errors[k]] for k in errors]
            else:
                m['errors'] = []
            
            #if latest_ok:
            #    m['s_ok'] = latest_ok
            
            #if latest_fail:
            #    m['s_fail'] = latest_fail
            
            # start off with arithmetic mean of scores... later, figure out
            # something more sensible
            if len(scores) > 0:
                m['score'] = score_sum / len(scores)
                if m['props']:
                    m['props']['score'] = m['score']
            
            # retain some properties
            prev_state = self.red.getServerStatus(id)
            if not prev_state or status != prev_state.get('status') or not prev_state.get('last_change'):
                m['last_change'] = m.get('last_test')
            else:
                m['last_change'] = prev_state.get('last_change')
            
            # update availability statistics
            if prev_state and 'last_test' in prev_state and 'last_test' in m:
                tdif = m['last_test'] - prev_state['last_test']
                if tdif > 0 and tdif < self.poll_interval * 3:
                    m['avail_7'], m['avail_30'] = self.red.updateAvail(id, tdif, m['status'] == 'ok')
                    
            self.log.debug("merged status for %s: %r", id, m)
            self.red.setServerStatus(id, m)
        
        return merged
    
    def update_dns(self, servers, merged_status):
        """
        Update DNS to match the current merged status
        """
        
        # Fetch setup from database (maintaned by config manager thread)
        rotates = self.red.getRotates()
        
        # Which servers are taking part in one of the rotations
        participating_servers = {}
        
        for d in rotates:
            if d in self.unmanaged_rotates:
                continue
            self.update_dns_rotate(d, rotates[d], merged_status, servers, participating_servers)
        
        # Push the addresses of individual servers
        self.update_dns_hosts(servers, merged_status)
        
        #self.log.debug("participating servers: %r", participating_servers)
        self.red.storeRotateStatus(participating_servers)
    
    def update_dns_rotate(self, domain, domain_conf, status, servers, participating_servers):
        """
        Update a single DNS rotate
        """
        self.log.info("Processing rotate %s ...", domain)
        #self.log.debug("Checking rotate %s: %r", domain, domain_conf)
        
        # which members are OK, and which of them have IPv4 or IPv6 addresses available
        members = domain_conf.get('members')
        members_ok = [i for i in members if status.get(i)
            and status.get(i).get('status') == 'ok' and status.get(i).get('score') != None
            and servers.get(i) and servers.get(i).get('out_of_service') != True
            and servers.get(i).get('deleted') != True]
            
        members_ok_v4 = [i for i in members_ok if servers.get(i).get('ipv4')]
        members_ok_v6 = [i for i in members_ok if servers.get(i).get('ipv6')]
        
        self.log.debug("Members: %r", members)
        self.log.debug("Members ok ip4: %r", members_ok_v4)
        self.log.debug("Members ok ip6: %r", members_ok_v6)
        
        # sort by score
        scored_order_v4 = sorted(members_ok_v4, key=lambda x:status.get(x).get('score'))
        scored_order_v6 = sorted(members_ok_v6, key=lambda x:status.get(x).get('score'))
        
        # Adjust the sizes of rotates: Number of entries * 0.7, so that
        # load balancing happens even in smaller rotates (the few servers with
        # the worst score are left out).
        v4_limit = int(round(len(scored_order_v4) * 0.7))
        v6_limit = int(round(len(scored_order_v6) * 0.7))
        
        # Maximum limit for the sizes of rotates
        # The DNS reply packet needs to be <= 512 bytes, since there are still
        # broken resolvers out there, which don't do EDNS or TCP.
        v4_limit = min(v4_limit, 8)
        v6_limit = min(v6_limit, 3)
        
        # Have at least 3 addresses in the rotate, anyway.
        v4_limit = max(v4_limit, 3)
        v6_limit = max(v6_limit, 3)
        
        limited_order_v4 = scored_order_v4[0:v4_limit]
        limited_order_v6 = scored_order_v6[0:v6_limit]
        
        self.log.info("Scored order ip4: %r", [(i, '%.1f' % status.get(i).get('score')) for i in limited_order_v4])
        self.log.info("Left out     ip4: %r", [(i, '%.1f' % status.get(i).get('score')) for i in scored_order_v4[v4_limit:]])
        self.log.info("Scored order ip6: %r", [(i, '%.1f' % status.get(i).get('score')) for i in limited_order_v6])
        self.log.info("Left out     ip6: %r", [(i, '%.1f' % status.get(i).get('score')) for i in scored_order_v6[v6_limit:]])
        
        if len(limited_order_v4) < 1:
            if domain == self.master_rotate:
                self.log.error("Ouch! Master rotate %s has no working servers - not doing anything!", self.master_rotate)
                return
            
            self.log.info("VERDICT %s: No working servers, CNAME %s", domain, self.master_rotate)
            self.dns_push(domain, domain, cname=self.master_rotate)
            return
        
        for i in list(set(limited_order_v4) | set(limited_order_v6)):
            h = participating_servers.get(i)
            if h:
            	h[domain] = 1
            else:
            	participating_servers[i] = { domain: 1 }
        
        # Addresses to use, ordered by score
        v4_addrs = [servers.get(i).get('ipv4') for i in limited_order_v4]
        v6_addrs = [servers.get(i).get('ipv6') for i in limited_order_v6]
        
        self.dns_push(domain, domain, v4_addrs=v4_addrs, v6_addrs=v6_addrs)
    
    def update_dns_hosts(self, servers, merged_status):
        """
        Push the addresses of individual servers to DNS
        """
        
        # which FQDNs should go in DNS, and which addresses for them
        names = {}
        # Which FQDNs should go in DNS, with CNAME pointing to rotate
        names_cnamed = {}
        
        for s in servers:
            serv = servers[s]
            #self.log.debug('Updating server %s: %r', s, serv)
            
            # TODO - use correct domain part!
            fqdn = serv.get('host') + '.' + serv.get('domain', '.')
            
            if serv.get('out_of_service') or serv.get('deleted'):
                names_cnamed[fqdn] = 1
            else:
                if not fqdn in names:
                    names[fqdn] = { 'v4': [], 'v6': [] }
                    
                if serv.get('ipv4'):
                    names[fqdn]['v4'].append(serv.get('ipv4'))
                if serv.get('ipv6'):
                    names[fqdn]['v6'].append(serv.get('ipv6'))
        
        for fqdn in names:
            self.dns_push(fqdn, fqdn, v4_addrs=names[fqdn]['v4'], v6_addrs=names[fqdn]['v6'])
        
        # Add CNAMEs to rotate, but only for names which did not get A records
        for fqdn in names_cnamed:
            if fqdn not in names:
                self.dns_push(fqdn, fqdn, cname=self.master_rotate)
        
    def dns_pick_zone(self, fqdn):
        """
        Figure out which zone to update, based on FQDN
        """
        
        for z in self.dns_zones:
            if fqdn.endswith('.' + z):
                return z
        
        return None
    
    def dns_push(self, logid, fqdn, v4_addrs = [], v6_addrs = [], cname = None):
        """
        Push a set of A and AAAA records to the DNS, but only if they've
        changed.
        """
        # check if there are any changes, sort the addresses first so that
        # scoring order changes do not cause cache misses
        v4_addrs = sorted(v4_addrs)
        v6_addrs = sorted(v6_addrs)
        if cname != None:
            cache_key = "CNAME " + cname
            v4_addrs = v6_addrs = []
        else:
            cache_key = ' '.join(v4_addrs) + ' ' + ' '.join(v6_addrs)
        
        if self.dns_update_cache.get(fqdn) == cache_key:
            #self.log.info("DNS push [%s]: %s - no changes", logid, fqdn)
            return
            
        self.dns_update_cache[fqdn] = cache_key
        
        # look up the zone file to update
        zone = self.dns_pick_zone(fqdn)
        if zone == None:
            self.log.info("DNS push [%s]: %s is not in a managed zone, not updating", logid, fqdn)
            return
        
        # add a dot to make sure bind doesn't add the zone name in the end
        fqdn = fqdn + '.'
        
        self.log.info("DNS pushing [%s]: %s: %s", logid, fqdn, cache_key)
        
        update = dns.update.Update(zone, keyring=self.dns_keyring, keyalgorithm="hmac-sha256")
        update.delete(fqdn)
        if cname != None:
            update.add(fqdn, self.dns_ttl, 'cname', cname + '.')
        else:
            for a in v4_addrs:
                update.add(fqdn, self.dns_ttl, 'a', a.encode('ascii'))
            for a in v6_addrs:
                update.add(fqdn, self.dns_ttl, 'aaaa', a.encode('ascii'))
        
        try:
            response = dns.query.tcp(update, self.dns_master)
        except socket.error as e:
            self.log.error("DNS push [%s]: update error, cannot connect to DNS master: %r", logid, e)
            return
        except dns.tsig.PeerBadKey as e:
            self.log.error("DNS push [%s]: update error, DNS master does not accept our key: %r", logid, e)
            return
        except Exception as e:
            self.log.error("DNS push [%s]: update error: %r", logid, e)
            return
            
        self.log.info("DNS push [%s]: Sent %s: %s - response: %s / %s", logid, zone, fqdn,
            dns.opcode.to_text(dns.opcode.from_flags(response.flags)),
            dns.rcode.to_text(dns.rcode.from_flags(response.flags, response.ednsflags))
            )
    
    def poll(self):
        """
        Do a single polling round
        """
        
        # Fetch full status JSON from all pollers, ignoring
        # pollers which appear to be faulty
        status_set = self.fetch_full_status()
        
        # Fetch setup from database (maintaned by config manager thread)
        servers = self.red.getServers()
        
        # Merge status JSONs, ignoring old polling results for individual servers,
        # figure out per-server "final score"
        merged_status = self.merge_status(servers, status_set)
        
        # Push current DNS status to the master, if it has changed
        self.update_dns(servers, merged_status)
        
        self.red.sendDnsStatusMessage({ 'reload': 'full' })
    
    def loop(self):
        """
        Main DNS driver loop
        """
        
        while True:
            self.poll()
            time.sleep(self.poll_interval)


cfgfile = 'poller.conf'
if len(sys.argv) > 1:
    cfgfile = sys.argv[1]

driver = DNSDriver(cfgfile)
driver.loop()

