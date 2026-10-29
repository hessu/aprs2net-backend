"""
Tests for the APRS-IS and IS2 port probes in aprsis.py, run against a
fake socket, and for the hand-written protobuf decoding of the IS2 server
signature.
"""

import logging
import socket

import pytest

import aprsis
from aprsis import pb_varint, pb_fields, is2_decode_server_signature, ProtobufError


class FakeSocket:
    """
    Serves recv() from a list of chunks, records what was sent, and can
    fail the connect.
    """
    def __init__(self, chunks, connect_exc=None):
        self.chunks = list(chunks)
        self.connect_exc = connect_exc
        self.sent = b''
        self.closed = False
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def connect(self, addr):
        self.addr = addr
        if self.connect_exc:
            raise self.connect_exc

    def recv(self, n):
        if not self.chunks:
            return b''
        chunk = self.chunks.pop(0)
        if len(chunk) > n:
            self.chunks.insert(0, chunk[n:])
            chunk = chunk[:n]
        return chunk

    def send(self, data):
        self.sent += data
        return len(data)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_socket(monkeypatch):
    """Install a FakeSocket factory; returns a setter for the next socket's data."""
    created = []

    def setup(chunks, connect_exc=None):
        def factory(af, kind):
            s = FakeSocket(chunks, connect_exc)
            s.af = af
            created.append(s)
            return s
        monkeypatch.setattr(aprsis.socket, 'socket', factory)
        return created
    return setup


LOG = logging.getLogger('test-aprsis')


# ---- plaintext APRS-IS ----

PROMPT = b'# aprsc 2.1.19\r\n'


def test_tcp_poll_ok(fake_socket):
    created = fake_socket([PROMPT, b'# logresp APRS2N-ET unverified, server T2TEST\r\n'])
    assert aprsis.TCPPoll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS4') == ['ok', 'Works fine!']
    s = created[0]
    assert s.af == socket.AF_INET
    assert s.addr == ('10.0.0.1', 14580)
    assert s.sent == b'user APRS2N-ET pass -1 vers aprs2net-poll 2.0\r\n'
    assert s.closed


def test_tcp_poll_ipv6_uses_inet6(fake_socket):
    created = fake_socket([PROMPT, b'# logresp APRS2N-ET unverified, server T2TEST\r\n'])
    aprsis.TCPPoll(LOG).poll('2001:db8::1', 14580, 'T2TEST', 'IS6')
    assert created[0].af == socket.AF_INET6


@pytest.mark.parametrize('prompt,response,code', [
    (b'', b'', 'IS4-acl'),
    (b'# Port full\r\n', b'', 'IS4-portfull'),
    (b'# Server full\r\n', b'', 'IS4-serverfull'),
    (PROMPT, b'garbage\r\n', 'IS4-unrecognized'),
    (PROMPT, b'# logresp N0CALL unverified, server T2TEST\r\n', 'IS4-login'),
    (PROMPT, b'# logresp APRS2N-ET verified, server T2TEST\r\n', 'IS4-verification'),
    (PROMPT, b'# logresp APRS2N-ET unverified, server T2OTHER\r\n', 'IS4-serverid'),
    (PROMPT, b'# logresp APRS2N-ET unverified, server T2TEST, adjunct "filter default" filter m/100\r\n', 'IS4-defaultfilter'),
])
def test_tcp_poll_failures(fake_socket, prompt, response, code):
    fake_socket([prompt, response])
    result = aprsis.TCPPoll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS4')
    assert result[0] == code


def test_tcp_poll_connection_refused(fake_socket):
    fake_socket([], connect_exc=ConnectionRefusedError(111, 'Connection refused'))
    result = aprsis.TCPPoll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS4')
    assert result[0] == 'IS4-socket'
    assert 'refused' in result[1]


def test_tcp_poll_firewalled(fake_socket):
    fake_socket([], connect_exc=PermissionError(13, 'Permission denied'))
    result = aprsis.TCPPoll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS4')
    assert result[0] == 'IS4-socket'
    assert 'firewalled' in result[1]


def test_tcp_poll_timeout(fake_socket):
    fake_socket([], connect_exc=socket.timeout('timed out'))
    result = aprsis.TCPPoll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS4')
    assert result[0] == 'IS4-socket'


# ---- protobuf ----

def enc_varint(n):
    out = b''
    while True:
        b = n & 0x7f
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def pb_var(field, n):
    return enc_varint((field << 3) | 0) + enc_varint(n)


def pb_len(field, data):
    if isinstance(data, str):
        data = data.encode('UTF-8')
    return enc_varint((field << 3) | 2) + enc_varint(len(data)) + data


def server_signature(username='T2TEST', app_name='aprsc', app_version='2.1.19', features=('is2',)):
    sig = pb_len(1, username) + pb_len(2, app_name) + pb_len(3, app_version)
    for f in features:
        sig += pb_len(4, f)
    return pb_var(1, aprsis.IS2_MSG_SERVER_SIGNATURE) + pb_len(21, sig)


def is2_frame(body):
    return bytes([aprsis.IS2_STX, 0, len(body) >> 8, len(body) & 0xff]) + body + bytes([aprsis.IS2_ETX])


@pytest.mark.parametrize('value', [0, 1, 127, 128, 300, 2 ** 32, 2 ** 63 - 1])
def test_varint_roundtrip(value):
    buf = enc_varint(value) + b'rest'
    assert pb_varint(buf, 0) == (value, len(buf) - 4)


def test_varint_truncated():
    with pytest.raises(ProtobufError):
        pb_varint(b'\x80\x80', 0)


def test_pb_fields_all_wire_types():
    buf = pb_var(1, 5) + pb_len(2, b'ab') + bytes([(3 << 3) | 1]) + b'12345678' + bytes([(4 << 3) | 5]) + b'1234'
    assert pb_fields(buf) == [(1, 0, 5), (2, 2, b'ab'), (3, 1, b'12345678'), (4, 5, b'1234')]


def test_pb_fields_truncated_length_field():
    with pytest.raises(ProtobufError):
        pb_fields(pb_len(2, b'abcdef')[:-2])


def test_pb_fields_unsupported_wire_type():
    with pytest.raises(ProtobufError):
        pb_fields(bytes([(1 << 3) | 3]))


def test_decode_server_signature():
    sig = is2_decode_server_signature(server_signature(features=('is2', 'xyz')))
    assert sig == {'username': 'T2TEST', 'app_name': 'aprsc', 'app_version': '2.1.19', 'features': ['is2', 'xyz']}


def test_decode_server_signature_ignores_unknown_fields():
    body = pb_var(1, aprsis.IS2_MSG_SERVER_SIGNATURE) + pb_var(2, 99) + pb_len(21, pb_len(1, 'T2TEST') + pb_len(9, 'future') + pb_var(5, 1))
    sig = is2_decode_server_signature(body)
    assert sig['username'] == 'T2TEST'
    assert sig['features'] == []


def test_decode_wrong_message_type():
    with pytest.raises(ProtobufError):
        is2_decode_server_signature(pb_var(1, 1) + pb_len(21, pb_len(1, 'T2TEST')))


def test_decode_signature_missing():
    with pytest.raises(ProtobufError):
        is2_decode_server_signature(pb_var(1, aprsis.IS2_MSG_SERVER_SIGNATURE))


def test_decode_bad_utf8():
    with pytest.raises(ProtobufError):
        is2_decode_server_signature(pb_var(1, 2) + pb_len(21, pb_len(1, b'\xff\xfe')))


# ---- IS2 port probe ----

def test_is2_poll_ok(fake_socket):
    frame = is2_frame(server_signature())
    # deliver in awkward chunks to exercise recv_exact
    created = fake_socket([frame[:1], frame[1:6], frame[6:]])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24') == ['ok', 'Works fine!']
    assert created[0].sent == b''
    assert created[0].closed


def test_is2_poll_plaintext_port(fake_socket):
    fake_socket([b'# aprsc 2.1.19\r\n'])
    result = aprsis.IS2Poll(LOG).poll('10.0.0.1', 14580, 'T2TEST', 'IS24')
    assert result[0] == 'IS24-plaintext'


def test_is2_poll_bad_framing(fake_socket):
    fake_socket([b'XXXX'])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-framing'

    body = server_signature()
    broken = is2_frame(body)[:-1] + b'\x00'
    fake_socket([broken])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-framing'

    fake_socket([bytes([aprsis.IS2_STX, 0, 0xff, 0xff])])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-framing'


def test_is2_poll_connection_closed_early(fake_socket):
    fake_socket([is2_frame(server_signature())[:10]])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-socket'


def test_is2_poll_wrong_server_id(fake_socket):
    fake_socket([is2_frame(server_signature(username='T2OTHER'))])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-serverid'


def test_is2_poll_signature_without_software(fake_socket):
    fake_socket([is2_frame(server_signature(app_version=''))])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-signature'


def test_is2_poll_undecodable(fake_socket):
    fake_socket([is2_frame(pb_var(1, 1))])
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-decode'


def test_is2_poll_timeout_and_refused(fake_socket):
    fake_socket([], connect_exc=socket.timeout('timed out'))
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-timeout'
    fake_socket([], connect_exc=ConnectionRefusedError(111, 'refused'))
    assert aprsis.IS2Poll(LOG).poll('10.0.0.1', 14581, 'T2TEST', 'IS24')[0] == 'IS24-socket'
