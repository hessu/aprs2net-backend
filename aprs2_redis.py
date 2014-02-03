
"""

aprs2.net server status poller

"""

import redis
import time
import json

kServer = 'aprs2.server'
kPollQueue = 'aprs2.pollq'

class APRS2Redis:
    def __init__(self, host='localhost', port=6379):
        """
        aprs2.net status storage in Redis
        """
        
        self.red = redis.Redis(host=host, port=port, db=0)
        
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
    
    def setPollQ(self, id, pollt):
    	"""
    	Set the next poll time for a server ID
    	"""
    	
    	return self.red.zadd(kPollQueue, id, pollt)
    
    def delPollQ(self, id):
    	"""
    	Remove a server from the polling queue
    	"""
    	
    	return self.red.zrem(kPollQueue, id)
    
    def getPollSet(self, max=4):
    	"""
    	Get a set of servers to poll
    	"""
    	
    	return self.red.zrangebyscore(kPollQueue, 0, time.time(), 0, max)
    






