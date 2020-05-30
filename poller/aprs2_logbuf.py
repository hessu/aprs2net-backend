
"""

This module implements a logger interface, which logs messages
using the given regular logger, but also buffers all the log messages
(up to a limit of X rows), and the buffer can be retrieved in the end.

It's used for storing per-server polling log records.

"""

import logging

FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

class PollingLog:
    def __init__(self, log, bufLen=1000):
        self.log = log
        self.buf = []
        self.buf_formatter = logging.Formatter(FORMAT, None)
    
    def do_append(self, level, msg, *args, **kwargs):
        """
        Format and buffer a log record.
        """
        record = self.log.makeRecord("poller", level, "(fn)", 0, msg, args, None, "()", None)
        s = self.buf_formatter.format(record)
        self.buf.append(s)
    
    def buffer_string(self):
        """
        Returns buffered log as a string.
        """
        return "\n".join(self.buf) + "\n"
        
    def debug(self, msg, *args, **kwargs):
        self.do_append(logging.DEBUG, msg, *args, **kwargs)
        self.log.debug(msg, *args, **kwargs)
    
    def info(self, msg, *args, **kwargs):
        self.do_append(logging.INFO, msg, *args, **kwargs)
        self.log.info(msg, *args, **kwargs)
        
    def warning(self, msg, *args, **kwargs):
        self.do_append(logging.WARNING, msg, *args, **kwargs)
        self.log.warning(msg, *args, **kwargs)
    
    def error(self, msg, *args, **kwargs):
        self.do_append(logging.ERROR, msg, *args, **kwargs)
        self.log.error(msg, *args, **kwargs)
    
    def exception(self, msg, *args, **kwargs):
        self.do_append(logging.EXCEPTION, msg, *args, **kwargs)
        self.log.exception(msg, *args, **kwargs)
    
    def critical(self, msg, *args, **kwargs):
        self.do_append(logging.CRITICAL, msg, *args, **kwargs)
        self.log.critical(msg, *args, **kwargs)
        
