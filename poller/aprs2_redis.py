
"""

aprs2.net server status poller

"""

import redis
import time
import json

kServer = 'aprs2.server'
kServerStatus = 'aprs2.serverstat'
kServerLog = 'aprs2.serverlog'
kPollQueue = 'aprs2.pollq'
kScore = 'aprs2.score'

class APRS2Redis:
    def __init__(self, host='localhost', port=6379):
        """
        aprs2.net status storage in Redis
        """
        self.red = redis.Redis(host=host, port=port, db=0)
    
    def setServerStatus(self, id, status):
        """
        Store server status
        """
    	return self.red.hset(kServerStatus, id, json.dumps(status))
    
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
    
