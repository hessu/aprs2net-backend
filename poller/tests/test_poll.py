"""
Tests for the server poller logic in aprs2_poll.py: status page parsing
for the three server software types, derived properties, clock, uplink and
IS2 checks. No network is involved.
"""

import logging
import time
import types

import pytest

import aprs2_poll
import aprsis
from aprs2_poll import Poll, javap3_strfloat, inet6_normalize


class FakeRedis:
    def __init__(self, servers=None):
        self.servers = servers or {}

    def getServer(self, id):
        return self.servers.get(id)


SERVER = {'id': 'T2TEST', 'host': 'test', 'ipv4': '10.0.0.1', 'ipv6': '2001:db8::1',
          'member': ['rotate.aprs2.net']}


@pytest.fixture
def poll():
    red = FakeRedis({
        'T2HUB1': {'id': 'T2HUB1', 'member': ['hubs.aprs2.net']},
        'T2CORE': {'id': 'T2CORE', 'member': ['rotate.aprs.net']},
    })
    address_map = {'10.0.0.5': 'T2HUB1', '2001:db8::5': 'T2HUB1'}
    return Poll(logging.getLogger('test-poll'), dict(SERVER), red, {}, {}, address_map)


# ---- helpers ----

@pytest.mark.parametrize('s,expected', [
    ('1,234', 1234.0), ('78.527.080', 78527080.0), ("78'527'080", 78527080.0), ('42', 42.0), ('1 234', 1234.0),
])
def test_javap3_strfloat(s, expected):
    assert javap3_strfloat(s) == expected


def test_inet6_normalize():
    assert inet6_normalize('2001:0db8:0000:0000:0000:0000:0000:0001') == '2001:db8::1'
    assert inet6_normalize('not an address') is None


@pytest.mark.parametrize('s,seconds', [
    ('132d18h34m27.215s', 132 * 86400 + 18 * 3600 + 34 * 60 + 27),
    ('5d14h00m45.881s', 5 * 86400 + 14 * 3600 + 45),
    ('00.025s', 0),
    ('12m', 720),
    ('', 0),
    ('garbage', 0),
])
def test_javap3_decode_uptime(poll, s, seconds):
    assert poll.javap3_decode_uptime(s) == seconds


def test_map_addr_id(poll):
    assert poll.map_addr_id('10.0.0.5:20152') == 'T2HUB1'
    assert poll.map_addr_id('2001:0db8:0000:0000:0000:0000:0000:0005:20152') == 'T2HUB1'
    assert poll.map_addr_id('10.0.0.99:20152') is None
    assert poll.map_addr_id('hub1.aprs2.net:20152') == 'unknown'


def test_error_collects_and_returns_false(poll):
    assert poll.error('x', 'boom') is False
    assert poll.errors == [['x', 'boom']]


# ---- aprsc ----

def aprsc_json(time_now=None, **server_extra):
    server = {'server_id': 'T2TEST', 'software': 'aprsc', 'software_version': '2.1.19', 'os': 'Linux',
              'uptime': 86400, 'is2': True}
    if time_now is not None:
        server['time_now'] = time_now
    server.update(server_extra)
    return {
        'server': server,
        'totals': {'clients': 500, 'clients_max': 1000, 'connects': 12345,
                   'tcp_bytes_tx': 1000, 'udp_bytes_tx': 10, 'tcp_bytes_rx': 2000, 'udp_bytes_rx': 20},
        'listeners': [
            {'proto': 'tcp', 'addr': '0.0.0.0:14580', 'clients': 400, 'clients_max': 500, 'is2_upgrade': True},
            {'proto': 'tcp', 'addr': '[::]:14580', 'clients': 10, 'clients_max': 500, 'is2_upgrade': True},
            {'proto': 'tcp', 'addr': '0.0.0.0:14581', 'clients': 5, 'clients_max': 500, 'is2': True},
            {'proto': 'tcp', 'addr': '0.0.0.0:10152', 'clients': 30, 'clients_max': 100, 'is2_upgrade': True},
            {'proto': 'tcp', 'addr': '0.0.0.0:10153', 'clients': 2, 'clients_max': 0, 'is2': True},
            {'proto': 'sctp', 'addr': '0.0.0.0:14580', 'clients': 0, 'clients_max': 500, 'is2': True},
            {'proto': 'udp', 'addr': '0.0.0.0:8080'},
        ],
        'uplinks': [{'username': 'T2HUB1', 'addr_rem': '10.0.0.5:20152', 'since_connect': 5000,
                     'since_last_read': 3, 'pkts_rx': 99, 'link': 'is2'}],
    }


def test_parse_aprsc(poll):
    assert poll.parse_aprsc(aprsc_json()) is True
    p = poll.properties
    assert p['id'] == 'T2TEST' and p['soft'] == 'aprsc' and p['vers'] == '2.1.19' and p['type'] == 'aprsc'
    assert p['is2_server'] is True
    assert p['clients'] == 500 and p['clients_max'] == 1000 and p['connects'] == 12345
    assert p['total_bytes_out'] == 1010 and p['total_bytes_in'] == 2020
    assert p['user_load'] == 50.0
    assert p['worst_load'] == 80.0       # the v4 14580 listener is 400/500
    assert p['fullfeed_clients'] == 32   # 10152 + 10153
    assert p['fullfeed_clients_max'] == 1100  # 100 + uncapped 1000
    assert p['fullfeed_load'] == 30.0
    assert p['uplinks'] == [{'id': 'T2HUB1', 'addr_rem': '10.0.0.5:20152', 'up': 5000,
                             'rx_last': 3, 'rx_packets': 99, 'link': 'is2'}]
    assert poll.check_properties() is True


def test_parse_aprsc_is2_listener_flags(poll):
    poll.parse_aprsc(aprsc_json())
    # only TCP listeners count: the SCTP 14580 listener does not make the port IS2-only
    assert poll.is2_listeners['14580'] == {'is2': False, 'is2_upgrade': True}
    assert poll.is2_listeners['14581']['is2'] is True
    assert poll.is2_listeners['10152']['is2_upgrade'] is True
    assert poll.is2_listeners['10153']['is2'] is True


def test_note_is2_listener_combines_families(poll):
    poll.note_is2_listener('14580', False, True)
    poll.note_is2_listener('14580', False, False)
    assert poll.is2_listeners['14580'] == {'is2': False, 'is2_upgrade': False}
    poll.note_is2_listener('14581', True, False)
    poll.note_is2_listener('14581', False, False)
    assert poll.is2_listeners['14581']['is2'] is True


@pytest.mark.parametrize('remove,code', [
    ('server', 'web-parse-fail'), ('totals', 'web-parse-fail'), ('listeners', 'web-parse-fail'),
])
def test_parse_aprsc_missing_blocks(poll, remove, code):
    j = aprsc_json()
    del j[remove]
    assert poll.parse_aprsc(j) is False
    assert poll.errors[0][0] == code


def test_parse_aprsc_missing_key(poll):
    j = aprsc_json()
    del j['server']['software_version']
    assert poll.parse_aprsc(j) is False
    assert 'software_version' in poll.errors[0][1]


def test_parse_aprsc_without_is2_or_uplinks(poll):
    j = aprsc_json()
    del j['server']['is2']
    del j['uplinks']
    assert poll.parse_aprsc(j) is True
    assert poll.properties['is2_server'] is False
    assert 'uplinks' not in poll.properties


def test_fullfeed_load_caps(poll):
    poll.set_fullfeed_load([(500, 2000), (10, -1)])
    p = poll.properties
    assert p['fullfeed_clients'] == 510
    assert p['fullfeed_clients_max'] == 2000   # both capped at 1000
    assert p['fullfeed_load'] == 50.0
    poll.properties = {}
    poll.set_fullfeed_load([])
    assert poll.properties == {}


# ---- clock ----

def test_clock_offset_within_request_window_is_zero(poll):
    poll.measure_aprsc_clock({'time_now': 1000.5}, t_start=1000.0, t_end=1001.0)
    assert poll.properties['clock_offset'] == 0.0
    assert poll.check_clock() is True


@pytest.mark.parametrize('time_now,offset', [(1004.0, 3.0), (997.0, -3.0), (1010.0, 9.0), (990.0, -10.0)])
def test_clock_offset_outside_window(poll, time_now, offset):
    poll.measure_aprsc_clock({'time_now': time_now}, t_start=1000.0, t_end=1001.0)
    assert poll.properties['clock_offset'] == offset
    ok = poll.check_clock()
    assert ok is (abs(offset) <= aprs2_poll.CLOCK_MAX_OFFSET)
    if not ok:
        assert poll.errors[0][0] == 'clock-offset'


def test_clock_not_reported(poll):
    poll.measure_aprsc_clock({}, 1000.0, 1001.0)
    assert 'clock_offset' not in poll.properties
    assert poll.check_clock() is False
    assert poll.errors[0][0] == 'clock-unknown'


def test_clock_not_numeric(poll):
    poll.measure_aprsc_clock({'time_now': 'soon'}, 1000.0, 1001.0)
    assert poll.check_clock() is False


def test_clock_check_passes_for_software_without_clock(poll):
    # javAPRSSrvr does not report one, so nothing is measured
    assert poll.check_clock() is True


# ---- javAPRSSrvr 4 ----

JAVAP4_XML = b'''<?xml version="1.0"?>
<javaprssrvr>
  <software version="4.3.0b01">javAPRSSrvr</software>
  <dupeprocessor><servercall>T2TEST</servercall></dupeprocessor>
  <java>
    <os architecture="amd64">Linux</os>
    <time><up millis="3600000"/><current utc="1700000000000"/></time>
  </java>
  <listenerports>
    <connections currentin="120" maximum="400"/>
    <portlistener><networkinterface port="14580"/><connections current="100" maximum="400"/></portlistener>
    <portlistener><networkinterface port="10152"/><connections current="20" maximum="50"/></portlistener>
  </listenerports>
  <clients total="5000">
    <rcvdtotals bytes="123456"/>
    <xmtdtotals bytes="654321"/>
    <clientrcv>
      <login><callssid>T2HUB1</callssid></login>
      <time><connect utc="1699990000000"/><lastlinein utc="1699999990000"/></time>
      <upstream>true</upstream>
      <rcvdfrom packets="777"/>
      <remoteserver port="20152">hub1.aprs2.net</remoteserver>
      <class name="UpstreamClientRcv"/>
    </clientrcv>
    <clientrcv>
      <login><callssid>N0CALL</callssid></login>
      <time><connect utc="1699990000000"/><lastlinein utc="1699999990000"/></time>
      <upstream>false</upstream>
      <class name="ClientRcv"/>
    </clientrcv>
  </clients>
</javaprssrvr>
'''


def test_parse_javaprssrvr4(poll):
    assert poll.parse_javaprssrvr4(JAVAP4_XML) is True
    p = poll.properties
    assert p['soft'] == 'javAPRSSrvr' and p['vers'] == '4.3.0b01' and p['type'] == 'javap4'
    assert p['id'] == 'T2TEST'
    assert p['os'] == 'Linux amd64'
    assert p['uptime'] == 3600
    assert p['clients'] == 120 and p['clients_max'] == 400 and p['connects'] == 5000
    assert p['total_bytes_in'] == 123456 and p['total_bytes_out'] == 654321
    assert p['user_load'] == 30.0 and p['worst_load'] == 30.0
    assert p['fullfeed_clients'] == 20 and p['fullfeed_clients_max'] == 50 and p['fullfeed_load'] == 40.0
    assert p['uplinks'] == [{'id': 'T2HUB1', 'addr_rem': 'hub1.aprs2.net:20152', 'up': 10000,
                             'rx_last': 10.0, 'rx_packets': 777}]
    assert poll.check_properties() is True


@pytest.mark.parametrize('bad,code', [
    (b'<not xml', 'web-xml-fail'),
    (b'<other/>', 'web-xml-fail'),
    (b'<other><x/></other>', 'web-parse-fail'),
    (JAVAP4_XML.replace(b'<software version="4.3.0b01">javAPRSSrvr</software>', b''), 'web-parse-fail'),
    (JAVAP4_XML.replace(b'<listenerports>', b'<lp>').replace(b'</listenerports>', b'</lp>'), 'web-parse-fail'),
])
def test_parse_javaprssrvr4_errors(poll, bad, code):
    assert poll.parse_javaprssrvr4(bad) is False
    assert poll.errors[0][0] == code


# ---- javAPRSSrvr 3 ----

JAVAP3_HTML = '''<HTML><BODY>
<TH>javAPRSSrvr 3.15b08<BR>Pete Loveall AE5PL</TH>
<TR><TD>Server ID</TD><TD>T2TEST</TD></TR>
<TR><TD>OS</TD><TD>Linux</TD></TR>
<TR><TD>Total Up Time</TD><TD>5d14h00m45.881s</TD></TR>
<TR><TD>Current Inbound Connections</TD><TD>1,234</TD></TR>
<TR><TD>Maximum Inbound Connections</TD><TD>3.000</TD></TR>
<TR><TD>Total Inbound Connects</TD><TD>99,999</TD></TR>
<TR><TD>Total Bytes In</TD><TD>78,527,080</TD></TR>
<TR><TD>Total Bytes Out</TD><TD>1'234'567</TD></TR>
<TABLE><TH>Outbound Connections</TH><TBODY><TR><TD>header</TD></TR>
<TR align=right><TD align=middle><A href="http://10.0.0.5:14501">hub1.aprs2.net/10.0.0.5:20152</A></TD><TD align=middle>C1BEF0E2</TD><TD align=middle>Yes</TD><TD align=middle>aprsc 2.0.11</TD><TD>5d14h00m45.881s</TD><TD>21,334,472</TD><TD>498,551</TD><TD>1,937,147,236</TD><TD>44,844,765</TD><TD>32,122</TD><TD>743</TD><TD>00.025s</TD><TD>4,048</TD><TD>0</TD></TR></TBODY></TABLE>
</BODY></HTML>'''


def test_parse_javaprssrvr3(poll):
    assert poll.parse_javaprssrvr3(JAVAP3_HTML) is True
    p = poll.properties
    assert p['id'] == 'T2TEST' and p['os'] == 'Linux' and p['type'] == 'javap3'
    assert p['soft'] == 'javAPRSSrvr' and p['vers'] == '3.15b08'
    assert p['uptime'] == 5 * 86400 + 14 * 3600 + 45
    # thousands separators in any locale
    assert p['clients'] == 1234.0 and p['clients_max'] == 3000.0 and p['connects'] == 99999.0
    assert p['total_bytes_in'] == 78527080.0 and p['total_bytes_out'] == 1234567.0
    assert round(p['user_load'], 2) == 41.13
    assert p['uplinks'] == [{'id': 'T2HUB1', 'addr_rem': '10.0.0.5:20152', 'up': 5 * 86400 + 14 * 3600 + 45,
                             'rx_packets': 21334472.0, 'rx_last': 0}]
    assert poll.check_properties() is True


def test_parse_javaprssrvr3_missing_field(poll):
    html = JAVAP3_HTML.replace('Total Up Time', 'Up Time')
    assert poll.parse_javaprssrvr3(html) is False
    assert poll.errors[0][0] == 'web-parse-fail'


# ---- properties and rates ----

def test_check_properties_id_mismatch(poll):
    poll.parse_aprsc(aprsc_json(server_id='T2OTHER'))
    assert poll.check_properties() is False
    assert poll.errors[0][0] == 'id-mismatch'


def test_check_properties_missing(poll):
    poll.properties = {'id': 'T2TEST'}
    assert poll.check_properties() is False
    assert poll.errors[0][0] == 'web-props'


def test_calculate_rates(poll, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(aprs2_poll.time, 'time', lambda: now[0])
    poll.properties = {'total_bytes_in': 1000, 'total_bytes_out': 2000, 'connects': 10}
    poll.calculate_rates()
    assert 'rate_bytes_in' not in poll.properties   # no previous sample
    assert poll.rates_cache['T2TEST'] == {'t': 1000.0, 'total_bytes_in': 1000, 'total_bytes_out': 2000, 'connects': 10}

    now[0] = 1010.0
    poll.properties = {'total_bytes_in': 1500, 'total_bytes_out': 2000, 'connects': 5}
    poll.calculate_rates()
    assert poll.properties['rate_bytes_in'] == 50.0
    assert 'rate_bytes_out' not in poll.properties   # unchanged
    assert 'rate_connects' not in poll.properties    # counter went backwards (restart)


# ---- uplink checks ----

def uplink(id='T2HUB1', rx_last=3):
    return {'id': id, 'addr_rem': '10.0.0.5:20152', 'up': 5000, 'rx_last': rx_last, 'rx_packets': 1}


def test_uplink_ok_for_rotate_member(poll):
    poll.properties['uplinks'] = [uplink()]
    assert poll.check_uplink() is True


@pytest.mark.parametrize('uplinks,code', [
    ([], 'uplinks-none'),
    ([uplink(), uplink()], 'uplinks-many'),
    ([uplink(id='T2NOBODY')], 'uplinks-odd'),
    ([uplink(id=None)], 'uplinks-odd'),
    ([uplink(id='T2CORE')], 'uplinks-wrong'),
    ([uplink(rx_last=301)], 'uplinks-stuck'),
])
def test_uplink_failures_for_rotate_member(poll, uplinks, code):
    poll.properties['uplinks'] = uplinks
    assert poll.check_uplink() is False
    assert poll.errors[0][0] == code


def test_hub_must_uplink_to_core(poll):
    poll.server['member'] = ['hubs.aprs2.net']
    poll.properties['uplinks'] = [uplink(id='T2CORE')]
    assert poll.check_uplink() is True
    poll.properties['uplinks'] = [uplink(id='T2HUB1')]
    assert poll.check_uplink() is False
    assert poll.errors[0][0] == 'uplinks-wrong'


def test_core_must_not_have_uplinks(poll):
    poll.server['member'] = ['rotate.aprs.net']
    poll.properties['uplinks'] = []
    assert poll.check_uplink() is True
    poll.properties['uplinks'] = [uplink()]
    assert poll.check_uplink() is False
    assert poll.errors[0][0] == 'uplinks-has'


def test_firenet_uplinks_not_tracked(poll):
    poll.server['member'] = ['firenet.aprs2.net']
    poll.properties['uplinks'] = [uplink(), uplink()]
    assert poll.check_uplink() is True


# ---- service, full feed and IS2 tests, with the port probes faked ----

@pytest.fixture
def fake_probes(monkeypatch):
    """
    Replace the TCP and IS2 port probes. results maps (port, family-prefix)
    to the [code, msg] to return; anything else is ok. calls records
    (host, port, prefix).
    """
    results = {}
    calls = []

    class FakeProbe:
        def __init__(self, log):
            pass

        def poll(self, host, port, serverid, logkey):
            calls.append((host, port, logkey))
            return results.get((port, logkey), ['ok', 'Works fine!'])
    monkeypatch.setattr(aprsis, 'TCPPoll', FakeProbe)
    monkeypatch.setattr(aprsis, 'IS2Poll', FakeProbe)
    return types.SimpleNamespace(results=results, calls=calls)


def test_service_tests_both_families(poll, fake_probes, monkeypatch):
    monkeypatch.setattr(poll, 'poll_http_submit', lambda: True)
    assert poll.service_tests() is True
    assert [c[1:] for c in fake_probes.calls] == [(14580, 'IS4'), (14580, 'IS6')]
    assert set(poll.score.poll_t_14580) == {'ipv4', 'ipv6'}


def test_service_tests_fail_if_one_family_fails(poll, fake_probes, monkeypatch):
    monkeypatch.setattr(poll, 'poll_http_submit', lambda: True)
    fake_probes.results[(14580, 'IS6')] = ['IS6-socket', 'refused']
    assert poll.service_tests() is False
    assert poll.errors[0][0] == 'IS6-socket'
    assert list(poll.score.poll_t_14580) == ['ipv4']


def test_hubs_are_tested_on_their_own_port(poll, fake_probes, monkeypatch):
    monkeypatch.setattr(poll, 'poll_http_submit', lambda: True)
    poll.id = 'T2HUB9'
    poll.service_tests()
    assert fake_probes.calls[0][1] == 20152


def test_fullfeed_tests(poll, fake_probes):
    poll.properties['fullfeed_clients'] = 5
    poll.fullfeed_tests()
    assert poll.properties['fullfeed'] is True
    assert set(poll.score.poll_t_fullfeed) == {'ipv4', 'ipv6'}

    fake_probes.results[(10152, 'FF6')] = ['FF6-socket', 'refused']
    poll.properties = {'fullfeed_clients': 5}
    poll.score.poll_t_fullfeed = {}
    poll.fullfeed_tests()
    assert 'fullfeed' not in poll.properties
    assert poll.properties['fullfeed_errors'][0][0] == 'FF6-socket'
    assert poll.score.poll_t_fullfeed == {}
    assert poll.errors == []   # not an error for the server as a whole


def test_fullfeed_tests_skipped_without_listener_or_membership(poll, fake_probes):
    poll.fullfeed_tests()
    assert fake_probes.calls == []
    poll.properties['fullfeed_clients'] = 5
    poll.server['member'] = []
    poll.fullfeed_tests()
    assert fake_probes.calls == []


def test_is2_tests_ok(poll, fake_probes):
    poll.parse_aprsc(aprsc_json())
    poll.properties['fullfeed'] = True
    poll.is2_tests()
    assert poll.properties['is2'] is True
    assert poll.properties['fullfeed_is2'] is True
    assert sorted(c[1:] for c in fake_probes.calls) == [
        (10153, 'FFIS24'), (10153, 'FFIS26'), (14581, 'IS24'), (14581, 'IS26')]


def test_is2_tests_server_without_is2(poll, fake_probes):
    j = aprsc_json()
    del j['server']['is2']
    poll.parse_aprsc(j)
    poll.is2_tests()
    assert 'is2' not in poll.properties
    assert poll.properties['is2_errors'][0][0] == 'is2-server'
    assert fake_probes.calls == []


@pytest.mark.parametrize('change,code', [
    (lambda j: j['uplinks'][0].update(link='plain'), 'is2-uplink'),
    (lambda j: j['listeners'][1].update(is2_upgrade=False), 'is2-upgrade'),
    (lambda j: j['listeners'].pop(2), 'is2-port'),
])
def test_is2_tests_requirements(poll, fake_probes, change, code):
    j = aprsc_json()
    change(j)
    poll.parse_aprsc(j)
    poll.is2_tests()
    assert 'is2' not in poll.properties
    assert [e[0] for e in poll.properties['is2_errors']] == [code]
    assert poll.errors == []


def test_is2_port_probe_failure(poll, fake_probes):
    fake_probes.results[(14581, 'IS26')] = ['IS26-timeout', 'timed out']
    poll.parse_aprsc(aprsc_json())
    poll.is2_tests()
    assert poll.properties['is2_errors'][0][0] == 'IS26-timeout'


def test_fullfeed_is2_needs_working_fullfeed(poll, fake_probes):
    poll.parse_aprsc(aprsc_json())
    poll.is2_tests()
    assert poll.properties['is2'] is True
    assert 'fullfeed_is2' not in poll.properties
    assert 'fullfeed_is2_errors' not in poll.properties


def test_fullfeed_is2_requirements(poll, fake_probes):
    j = aprsc_json()
    j['listeners'].pop(4)   # no 10153
    poll.parse_aprsc(j)
    poll.properties['fullfeed'] = True
    poll.is2_tests()
    assert poll.properties['is2'] is True
    assert [e[0] for e in poll.properties['fullfeed_is2_errors']] == ['is2-port']


# ---- the whole poll ----

def test_poll_scores_failure(poll, monkeypatch):
    monkeypatch.setattr(poll, 'poll_main', lambda: poll.error('web-http-fail', 'down'))
    assert poll.poll() is False
    assert poll.properties['score'] == 1000
    assert poll.properties['scorebase']['server-fail'] == [1000, '1000']


def test_poll_scores_success_with_fullfeed(poll, monkeypatch):
    def main():
        poll.score.http_status_t = 0.1
        poll.score.poll_t_14580 = {'ipv4': 0.1}
        poll.score.poll_t_fullfeed = {'ipv4': 0.1}
        poll.properties.update({'worst_load': 10.0, 'fullfeed_load': 20.0, 'fullfeed': True})
        return True
    monkeypatch.setattr(poll, 'poll_main', main)
    assert poll.poll() is True
    assert poll.properties['score'] == 100.0
    assert poll.properties['fullfeed_score'] == 200.0
    assert poll.properties['fullfeed_scorebase']['user_load'] == [200.0, '20.0 %']


def test_poll_main_rejects_old_software(poll, monkeypatch):
    monkeypatch.setattr(poll, 'ping', lambda: True)
    monkeypatch.setattr(poll, 'poll_javaprssrvr3', lambda: None)
    monkeypatch.setattr(poll, 'poll_javaprssrvr4', lambda: None)

    def aprsc():
        poll.score.http_status_t = 0.1
        return poll.parse_aprsc(aprsc_json(software_version='2.0.14'))
    monkeypatch.setattr(poll, 'poll_aprsc', aprsc)
    assert poll.poll_main() is False
    assert poll.errors[-1][0] == 'soft-old'
    assert poll.software_type_cache['T2TEST'] == 'aprsc'


def test_poll_main_tries_cached_software_type_first(poll, monkeypatch):
    order = []
    monkeypatch.setattr(poll, 'ping', lambda: True)
    for t in ('javap3', 'javap4', 'aprsc'):
        monkeypatch.setattr(poll, 'poll_' + {'javap3': 'javaprssrvr3', 'javap4': 'javaprssrvr4', 'aprsc': 'aprsc'}[t],
            (lambda t: lambda: order.append(t))(t))
    poll.software_type_cache['T2TEST'] = 'aprsc'
    poll.poll_main()
    assert order[0] == 'aprsc'
    assert poll.errors[-1][0] == 'web-undetermined'
