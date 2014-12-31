
"""

aprs2.net server status poller

"""

import redis
import time
import json

kServer = 'aprs2.server'
kAddressMap = 'aprs2.addrmap'
kServerStatus = 'aprs2.serverstat'
kServerLog = 'aprs2.serverlog'
kPollQueue = 'aprs2.pollq'
kScore = 'aprs2.score'
kAvail = 'aprs2.avail'
kChannelStatus = 'aprs2.chStatus'
kChannelStatusDns = 'aprs2.chStatusDns'
kWebConfig = 'aprs2.webconfig'
kRotate = 'aprs2.rotate'
kRotateStatus = 'aprs2.rotateStatus'
kRotateStats = 'aprs2.rotateStats'

def lsum(list):
    d = 0
    for v in list:
       if v != None:
           d += int(v)
    return d

class APRS2Redis:
    def __init__(self, host='localhost', port=6379, db=0):
        """
        aprs2.net status storage in Redis
        """
        self.red = redis.Redis(host=host, port=port, db=db)
    
    def setWebConfig(self, conf):
        """
        Store web UI config
        """
    	return self.red.set(kWebConfig, json.dumps(conf))
    
    def setAddressMap(self, map):
        """
        Store IP address => server ID map
        """
    	return self.red.set(kAddressMap, json.dumps(map))
    
    def getAddressMap(self):
        """
        Store IP address => server ID map
        """
    	d = self.red.get(kAddressMap)
    	if d == None:
    	    return {}
    	
    	return json.loads(d)
    
    def sendServerStatusMessage(self, msg):
        self.red.publish(kChannelStatus, json.dumps(msg))
    
    def sendDnsStatusMessage(self, msg):
        self.red.publish(kChannelStatusDns, json.dumps(msg))
    
    def setServerStatus(self, id, status):
        """
        Store server status
        """
        return self.red.hset(kServerStatus, id, json.dumps(status))
        
    def delServer(self, id):
        self.red.hdel(kServer, id)
        self.red.hdel(kServerStatus, id)
        self.red.hdel(kServerLog, id)
    
    def getServerIds(self):
        return self.red.hkeys(kServer)
        
    def getServerStatus(self, id):
    	"""
    	Get a single server configuration
    	"""
    	
    	d = self.red.hget(kServerStatus, id)
    	if d == None:
    		return d
    	
    	return json.loads(d)
    
    def storeServer(self, server):
    	"""
    	Store a single server configuration
    	"""
    	return self.red.hset(kServer, server['id'], json.dumps(server))
    
    def getServer(self, id):
    	"""
    	Get a single server configuration
    	"""
    	
    	d = self.red.hget(kServer, id)
    	if d == None:
    	    return d
    	
    	return json.loads(d)
    
    def getServers(self):
    	"""
    	Get all server configurations
    	"""
    	
    	d = self.red.hgetall(kServer)
    	if d == None:
    		return d
    	
    	o = {}
    	for k in d:
    	    o[k] = json.loads(d[k])
    	
    	return o
    
    def storeServerLog(self, id, logEntry):
    	"""
    	Store a single server configuration
    	"""
    	return self.red.hset(kServerLog, id, json.dumps(logEntry))
        
    def setPollQ(self, id, pollt):
    	"""
    	Set the next poll time for a server ID
    	"""
    	
    	return self.red.zadd(kPollQueue, id, pollt)
    
    def getPollQ(self, id):
    	"""
    	Get the next poll time for a server ID
    	"""
    	
    	return self.red.zscore(kPollQueue, id)
    
    def delPollQ(self, id):
    	"""
    	Remove a server from the polling queue
    	"""
    	
    	return self.red.zrem(kPollQueue, id)
    
    def getPollList(self):
        """
        Get the full set of servers in polling queue
        """
        
        return self.red.zrange(kPollQueue, 0, -1)
    
    def getPollSet(self, max=4):
    	"""
    	Get a set of servers to poll
    	"""
    	
    	return self.red.zrangebyscore(kPollQueue, 0, time.time(), 0, max)
    
    def setScore(self, id, score):
    	"""
    	Set the score for a server ID
    	"""
    	
    	return self.red.zadd(kScore, id, pollt)
    
    def delScore(self, id):
    	"""
    	Remove a server from the scoring
    	"""
    	
    	return self.red.zrem(kScore, id)
    
    def storeRotate(self, id, rot):
    	"""
    	Store a single rotate configuration
    	"""
    	return self.red.hset(kRotate, id, json.dumps(rot))
    
    def getRotates(self):
    	"""
    	Get a all rotate configurations
    	"""
    	
    	d = self.red.hgetall(kRotate)
    	if d == None:
    		return d
    	
    	o = {}
    	for k in d:
    	   o[k] = json.loads(d[k])

    	return o
    
    def storeRotateStatus(self, rot):
    	"""
    	Store a single rotate status
    	"""
    	return self.red.set(kRotateStatus, json.dumps(rot))
    
    def storeRotateStats(self, domain, stats):
    	"""
    	Store statistics for a single rotate
    	"""
    	return self.red.hset(kRotateStats, domain, json.dumps(stats))
    
    def updateAvail(self, id, seconds, isUp):
    	"""
    	Update the availability status of a server (N seconds, up or down).
    	Returns current availability statistics, too.
    	"""
    	
    	# which day is it?
    	now = int(time.time())
    	now_day = now - (now % 86400)
    	
    	# update stats
    	if isUp:
    	    hkey = '%s.%d.up' % (id, now_day)
    	else:
    	    hkey = '%s.%d.down' % (id, now_day)
    	    
    	self.red.hincrby(kAvail, hkey, seconds)
    	#print "availability: %s for %d seconds" % (hkey, seconds)
    	
    	# calculate 30-day availability
    	upkeys = ['%s.%d.up' % (id, now_day - i*86400) for i in range(0, 30)]
    	#print "upkeys: %r" % upkeys
    	upvals = self.red.hmget(kAvail, upkeys)
    	
    	downkeys = ['%s.%d.down' % (id, now_day - i*86400) for i in range(0, 30)]
    	#print "downkeys: %r" % downkeys
    	downvals = self.red.hmget(kAvail, downkeys)
    	
    	uptime_30 = lsum(upvals)
    	downtime_30 = lsum(downvals)
    	avail_30 = float(uptime_30) / (uptime_30 + downtime_30) * 100.0
    	
    	# For 3-day availability, we take today, 2 days before, and a fraction
    	# of the 3rd day, fraction depending on how far into 'today' we are.
    	# This will soften the fluctuation at midnight UTC, when a full 24 hours of
    	# availability was removed from the equation.
    	first_day_fraction =  (1.0 - (now % 86400 / 86400.0))
    	uptime_3 = lsum(upvals[0:3]) + lsum(upvals[3:4]) * first_day_fraction
    	downtime_3 = lsum(downvals[0:3]) + lsum(downvals[3:4]) * first_day_fraction
    	avail_3 = float(uptime_3) / (uptime_3 + downtime_3) * 100.0
    	
    	print "uptime %d seconds, downtime %d seconds - availability %.1f %%" \
    	    % (uptime_30, downtime_30, avail_30)
    	
    	# expire old keys
    	delkeys = ['%s.%d.up' % (id, now_day - i*86400) for i in range(31, 38)]
    	delkeys.extend(['%s.%d.down' % (id, now_day - i*86400) for i in range(31, 38)])
    	self.red.hdel(kAvail, delkeys)
    	
    	return (avail_3, avail_30)
    
