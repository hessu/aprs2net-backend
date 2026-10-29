
"""
APRS-IS testing client for the purpose of checking if a server is working
"""

import socket
import re

re_prompt_port_full = re.compile('# Port full')
re_prompt_server_full = re.compile('# Server full')
re_login_ok = re.compile('# logresp ([^ ]+) ([^, ]+), server ([A-Z0-9\\-]+)')

class TCPPoll:
    def __init__(self, log):
        self.log = log
        self.sock_timeout = 5
        self.mycall = 'APRS2N-ET'
    
    def poll(self, host, port, serverid, logkey):
        """
        Test that an APRS-IS server is responsive
        """
        self.id = serverid
        self.host = host
        self.port = port
        self.logkey = logkey
        
        self.log.info("%s: APRS-IS TCP test: %s port %s", self.id, host, port)
        
        if ':' in host:
            af = socket.AF_INET6
        else:
            af = socket.AF_INET
        
        s = socket.socket(af, socket.SOCK_STREAM)
        s.settimeout(self.sock_timeout)
        
        prompt = None
        login_ok = ""
        
        try:
            s.connect((host, port))
            prompt = s.recv(1024).decode('UTF-8')
            self.log.debug('%s: Login prompt: %s', self.id, repr(prompt))
            login_command = "user %s pass -1 vers aprs2net-poll 2.0\r\n" % self.mycall
            s.send(login_command.encode('ASCII'))
            login_ok = s.recv(1024).decode('UTF-8')
            self.log.debug('%s: Login response: %s', self.id, repr(login_ok))
            s.close()
        except IOError as e:
            try:
                s.close()
            except Exception:
                pass
            s = None
            if e.errno == 13:
                return self.error('socket', "APRS-IS port firewalled: %s" % e)
            else:
                return self.error('socket', "APRS-IS socket error: %s" % e)
        except socket.error as e:
            try:
                s.close()
            except Exception:
                pass
            s = None
            return self.error('socket', "APRS-IS socket error: %s" % e)
        
        s = None
        
        if prompt == "":
            return self.error('acl', 'Server closed connection immediately without sending version string (ACL?)')
        
        m = re_prompt_port_full.search(prompt)
        if m != None:
            self.log.info('%s: Login prompt says port is full: %s', self.id, repr(prompt))
            return self.error('portfull', "APRS-IS port full, too many clients")
        
        m = re_prompt_server_full.search(prompt)
        if m != None:
            self.log.info('%s: Login prompt says server is full: %s', self.id, repr(prompt))
            return self.error('serverfull', "APRS-IS server full, too many clients")

        m = re_login_ok.search(login_ok)
        if m == None:
            self.log.info('%s: Login response not recognized: %s', self.id, repr(login_ok))
            return self.error('unrecognized', "APRS-IS login response line not recognized")
        
        my_back = m.group(1)
        verif_s = m.group(2)
        serverid_back = m.group(3)
        
        if my_back != self.mycall:
            return self.error('login', "APRS-IS login response does not contain my callsign %s" % self.mycall)
        
        if verif_s != 'unverified':
            return self.error('verification', "APRS-IS login response is not 'unverified' for pass -1: got '%s'" % verif_s)
        
        if serverid_back != serverid:
            return self.error('serverid', "APRS-IS login response for '%s' has unexpected server ID: '%s'" % (serverid, serverid_back))
        
        if login_ok.find('adjunct "filter default" filter') > 0:
            return self.error('defaultfilter', "APRS-IS login response for '%s' says a default filter is configured" % (serverid,))
        
        self.log.info("%s: APRS-IS TCP OK: %s port %s", self.id, host, port)
        
        return ['ok', 'Works fine!']
        
    def error(self, code, msg):
        code = '%s-%s' % (self.logkey, code)
        self.log.info("%s: APRS-IS TCP FAIL: %s port %s: %s", self.id, self.host, self.port, msg)
        return [code, msg]


#
# IS2 framing and the server signature, as described in aprsc's doc/IS2.md
# and src/aprsis2.proto. An IS2 frame on TCP is an STX byte, a reserved
# byte, a 16-bit big-endian body length, a Protocol Buffers IS2Message,
# and an ETX byte. A server sends an IS2ServerSignature right after
# accepting a connection on an IS2 port. Only that one message is decoded
# here, by hand, to keep the poller free of generated protobuf code.
#

IS2_STX = 0x02
IS2_ETX = 0x03
IS2_HEAD_LEN = 4
IS2_TAIL_LEN = 1
IS2_MAXIMUM_FRAME_LEN = 4096

# IS2Message.Type
IS2_MSG_SERVER_SIGNATURE = 2

# Protocol Buffers wire types
PB_VARINT = 0
PB_FIXED64 = 1
PB_LENGTH = 2
PB_FIXED32 = 5

class ProtobufError(Exception):
    pass

def pb_varint(buf, pos):
    """
    Decode a varint from buf at pos. Returns (value, new_pos).
    """
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtobufError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7f) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise ProtobufError("varint too long")

def pb_fields(buf):
    """
    Decode the fields of a Protocol Buffers message, returning a list of
    (field_number, wire_type, value). Varints come out as ints, length
    delimited fields as bytes, and fixed-width fields as bytes.
    """
    fields = []
    pos = 0
    while pos < len(buf):
        key, pos = pb_varint(buf, pos)
        field = key >> 3
        wtype = key & 0x07
        if wtype == PB_VARINT:
            val, pos = pb_varint(buf, pos)
        elif wtype == PB_LENGTH:
            l, pos = pb_varint(buf, pos)
            if pos + l > len(buf):
                raise ProtobufError("truncated length-delimited field %d" % field)
            val = buf[pos:pos+l]
            pos += l
        elif wtype == PB_FIXED64:
            val = buf[pos:pos+8]
            pos += 8
        elif wtype == PB_FIXED32:
            val = buf[pos:pos+4]
            pos += 4
        else:
            raise ProtobufError("unsupported wire type %d for field %d" % (wtype, field))
        if pos > len(buf):
            raise ProtobufError("truncated fixed-width field %d" % field)
        fields.append((field, wtype, val))
    return fields

def pb_string(val, what):
    try:
        return val.decode('UTF-8')
    except (UnicodeDecodeError, AttributeError):
        raise ProtobufError("%s is not a valid UTF-8 string" % what)

def is2_decode_server_signature(body):
    """
    Decode an IS2Message body, which must carry a SERVER_SIGNATURE.
    Returns a dict with username, app_name, app_version and features.
    Raises ProtobufError if the message is something else or malformed.
    """
    msg_type = None
    sig = None
    
    for field, wtype, val in pb_fields(body):
        if field == 1 and wtype == PB_VARINT:
            msg_type = val
        elif field == 21 and wtype == PB_LENGTH:
            sig = val
        # other fields of IS2Message are ignored, as a protobuf decoder would
    
    if msg_type != IS2_MSG_SERVER_SIGNATURE:
        raise ProtobufError("IS2Message type is %r, expected SERVER_SIGNATURE (%d)" % (msg_type, IS2_MSG_SERVER_SIGNATURE))
    
    if sig == None:
        raise ProtobufError("IS2Message of type SERVER_SIGNATURE has no server_signature")
    
    r = { 'username': '', 'app_name': '', 'app_version': '', 'features': [] }
    for field, wtype, val in pb_fields(sig):
        if wtype != PB_LENGTH:
            continue
        if field == 1:
            r['username'] = pb_string(val, 'username')
        elif field == 2:
            r['app_name'] = pb_string(val, 'app_name')
        elif field == 3:
            r['app_version'] = pb_string(val, 'app_version')
        elif field == 4:
            r['features'].append(pb_string(val, 'features'))
    
    return r

class IS2Poll:
    """
    Test that an IS2 port is reachable and speaks IS2: connect, read the
    server signature frame the server sends right away, check it, and
    disconnect without logging in.
    """
    def __init__(self, log):
        self.log = log
        self.sock_timeout = 5
    
    def recv_exact(self, s, n):
        """
        Read exactly n bytes from the socket, or raise IOError if the
        connection closes first.
        """
        buf = b''
        while len(buf) < n:
            d = s.recv(n - len(buf))
            if not d:
                raise IOError("connection closed after %d of %d bytes" % (len(buf), n))
            buf += d
        return buf
    
    def poll(self, host, port, serverid, logkey):
        self.id = serverid
        self.host = host
        self.port = port
        self.logkey = logkey
        
        self.log.info("%s: IS2 TCP test: %s port %s", self.id, host, port)
        
        if ':' in host:
            af = socket.AF_INET6
        else:
            af = socket.AF_INET
        
        s = socket.socket(af, socket.SOCK_STREAM)
        s.settimeout(self.sock_timeout)
        
        try:
            s.connect((host, port))
            head = self.recv_exact(s, IS2_HEAD_LEN)
            if head[0] != IS2_STX:
                # a plaintext APRS-IS port would have sent "# aprsc ..."
                if head[0:1] == b'#':
                    head += s.recv(80)
                    s.close()
                    return self.error('plaintext', "Not an IS2 port, got a plaintext APRS-IS banner: %r" % head.split(b'\r')[0])
                s.close()
                return self.error('framing', "IS2 frame does not start with STX: %r" % head)
            body_len = (head[2] << 8) | head[3]
            if body_len + IS2_HEAD_LEN + IS2_TAIL_LEN > IS2_MAXIMUM_FRAME_LEN:
                s.close()
                return self.error('framing', "IS2 frame body length %d too large" % body_len)
            body = self.recv_exact(s, body_len)
            tail = self.recv_exact(s, IS2_TAIL_LEN)
            s.close()
        except socket.timeout:
            try:
                s.close()
            except Exception:
                pass
            return self.error('timeout', "IS2 socket timed out waiting for server signature")
        except (IOError, socket.error) as e:
            try:
                s.close()
            except Exception:
                pass
            if getattr(e, 'errno', None) == 13:
                return self.error('socket', "IS2 port firewalled: %s" % e)
            return self.error('socket', "IS2 socket error: %s" % e)
        
        s = None
        
        if tail[0] != IS2_ETX:
            return self.error('framing', "IS2 frame does not end with ETX: %r" % tail)
        
        try:
            sig = is2_decode_server_signature(body)
        except ProtobufError as e:
            return self.error('decode', "IS2 server signature decoding failed: %s" % e)
        
        self.log.debug('%s: IS2 server signature: %r', self.id, sig)
        
        if sig['username'] != serverid:
            return self.error('serverid', "IS2 server signature for '%s' has unexpected server ID: '%s'" % (serverid, sig['username']))
        
        if not sig['app_name'] or not sig['app_version']:
            return self.error('signature', "IS2 server signature does not give the software name and version")
        
        self.log.info("%s: IS2 TCP OK: %s port %s: %s %s", self.id, host, port, sig['app_name'], sig['app_version'])
        
        return ['ok', 'Works fine!']
    
    def error(self, code, msg):
        code = '%s-%s' % (self.logkey, code)
        self.log.info("%s: IS2 TCP FAIL: %s port %s: %s", self.id, self.host, self.port, msg)
        return [code, msg]
