
import queue
import threading

import graphitesend

# don't hold a massive backlog, just momentary spikes
max_queue_size = 500

g_thread = None

class GraphiteThread(object):
    def __init__(self, log):
        self.log = log
        self.graphite = None
        self.thr = None
        
        
        self.queue = queue.Queue(max_queue_size)
        self.stopping = threading.Event()
        
        self.check_connect()
        
        t = threading.Thread(target = self.__consume)
        t.daemon = True
        t.start()
        
    def check_connect(self):
        if self.graphite == None or self.graphite.socket == None:
            try:
                self.log.info("Connecting to Graphite")
                self.graphite = graphitesend.GraphiteClient(fqdn_squash=True, graphite_server='t2graph.aprs2.net', graphite_port=2003)
            except Exception as e:
                self.log.error("Failed to connect to Graphite: %r" % e)
                return
    
    def __consume(self):
        while not self.stopping.is_set():
            try:
                item = self.queue.get(block = True, timeout = 1)
                self.check_connect()
                
                metric, value = item
                self.transmit(metric, value)
                
                self.queue.task_done()
            except queue.Empty:
                pass
            except Exception as e:
                import traceback
                self.log.error("GraphiteThread: %s", traceback.format_exc())
                self.queue.task_done()
                
        self.log.debug("GraphiteThread stopping")

    def transmit(self, metric, value):
        if self.graphite == None:
            return
            
        try:
            self.graphite.send(metric, value)
        except graphitesend.GraphiteSendException:
            self.log.exception("Graphite send failed")
            try:
                self.graphite.disconnect()
            except Exception:
                pass
            self.graphite = None

class GraphiteSender(object):
    def __init__(self, log, fqdn):
        self.log = log
        
        global g_thread
        if g_thread == None:
            g_thread = GraphiteThread(log)
        
        # remove domain from fqdn
        hostname = fqdn
        #i = hostname.find('.')
        #if i >= 0:
        #    hostname = hostname[0:i]
        
        self.hostname = hostname

    def send(self, metric, value):
        global g_thread
        if g_thread.graphite == None:
            # don't even queue
            return

        try:
            g_thread.queue.put(('aprs2.%s.%s' % (self.hostname, metric), value), block = True, timeout = 0.1)
            return True
        except Queue.Full:
            self.log.error("GraphiteSender: queue full")
            return False

