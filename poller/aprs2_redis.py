
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
kChannelStatus = 'aprs2.chStatus'
kWebConfig = 'aprs2.webconfig'
kRotate = 'aprs2.rotate'
kRotateStatus = 'aprs2.rotateStatus'

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
    
    def storeRotateStatus(self, id, rot):
    	"""
    	Store a single rotate status
    	"""
    	return self.red.hset(kRotateStatus, id, json.dumps(rot))
    
    def getRotateStatus(self):
    	"""
    	Get full rotate status
    	"""
    	d = self.red.hgetall(kRotateStatus)
    	if d == None:
    		return d
    	
    	o = {}
    	for k in d:
    	   o[k] = json.loads(d[k])

    	return o
