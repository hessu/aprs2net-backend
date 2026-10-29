"""
Tests for the portal configuration processing in aprs2_config.py.
"""

import logging

import pytest

from aprs2_config import ConfigManager


class FakeRedis:
    def __init__(self):
        self.rotates = {}
        self.servers = {}
        self.pollq = {}
        self.address_map = None
        self.log = []

    def storeRotate(self, id, rot):
        self.rotates[id] = rot

    def storeServer(self, server):
        self.servers[server['id']] = server

    def delServer(self, id):
        self.servers.pop(id, None)
        self.log.append(('delServer', id))

    def getServerIds(self):
        return list(self.servers)

    def setPollQ(self, id, t):
        self.pollq[id] = t

    def getPollQ(self, id):
        return self.pollq.get(id)

    def delPollQ(self, id):
        self.pollq.pop(id, None)
        self.log.append(('delPollQ', id))

    def getPollList(self):
        return list(self.pollq)

    def setAddressMap(self, m):
        self.address_map = m


@pytest.fixture
def red():
    return FakeRedis()


@pytest.fixture
def manager(red):
    return ConfigManager(logging.getLogger('test-config'), red, 'http://servers', 'http://rotates',
        unmanaged_rotates={'firenet.aprs2.net': 1})


def portal_json():
    return {
        'rotate.aprs2.net': {
            'descr': 'Tier 2',
            'servers': {
                't2test': {'host': 'test', 'ipv4': '10.0.0.1', 'ipv6': '2001:db8::1'},
                'T2SIX': {'host': 'six', 'ipv6': '2001:db8::6'},
                'T2OLD': {'host': 'old', 'ipv4': '10.0.0.9', 'deleted': True},
                'T2POLL-FI': {'host': 'poller', 'ipv4': '10.0.0.100'},
            },
        },
        'hubs.aprs2.net': {
            'descr': 'Hubs',
            'servers': {
                't2test': {'host': 'test', 'ipv4': '10.0.0.1', 'ipv6': '2001:db8::1'},
                'T2HUB1': {'host': 'hub1', 'ipv4': '10.0.0.5'},
            },
        },
        'firenet.aprs2.net': {
            'descr': 'Firenet',
            'servers': {
                'T2FIRE': {'host': 'fire', 'ipv4': '10.0.0.7'},
            },
        },
        't2poll.aprs2.net': {
            'descr': 'Pollers',
            'servers': {'T2POLL-FI': {'host': 'poller', 'ipv4': '10.0.0.100'}},
        },
    }


def test_rotates_stored_with_members(manager, red):
    manager.process_config_json(portal_json())
    assert set(red.rotates) == {'rotate.aprs2.net', 'hubs.aprs2.net', 'firenet.aprs2.net'}
    assert red.rotates['rotate.aprs2.net']['members'] == ['t2test', 'T2SIX', 'T2OLD']
    assert red.rotates['hubs.aprs2.net']['members'] == ['t2test', 'T2HUB1']
    assert red.rotates['rotate.aprs2.net']['descr'] == 'Tier 2'
    assert 'servers' not in red.rotates['rotate.aprs2.net']


def test_servers_stored_uppercased_with_memberships(manager, red):
    manager.process_config_json(portal_json())
    assert set(red.servers) == {'T2TEST', 'T2OLD', 'T2HUB1', 'T2FIRE'}
    t = red.servers['T2TEST']
    assert t['id'] == 'T2TEST'
    assert t['member'] == ['rotate.aprs2.net', 'hubs.aprs2.net']
    assert t['show_members'] == 1
    # a server only in an unmanaged rotate does not show its memberships
    assert 'show_members' not in red.servers['T2FIRE']


def test_ipv6_only_server_is_dropped(manager, red):
    red.servers['T2SIX'] = {'id': 'T2SIX'}
    red.pollq['T2SIX'] = 1
    manager.process_config_json(portal_json())
    assert 'T2SIX' not in red.servers
    assert 'T2SIX' not in red.pollq


def test_pollers_are_not_servers(manager, red):
    manager.process_config_json(portal_json())
    assert 'T2POLL-FI' not in red.servers
    assert 't2poll.aprs2.net' not in red.rotates


def test_poll_queue(manager, red, monkeypatch):
    monkeypatch.setattr('aprs2_config.time.time', lambda: 1000)
    monkeypatch.setattr('aprs2_config.random.randint', lambda a, b: 7)
    red.pollq = {'T2HUB1': 500, 'T2GONE': 600}
    red.servers = {'T2GONE': {'id': 'T2GONE'}}
    manager.process_config_json(portal_json())
    # new servers are queued with a random spread, existing ones keep their slot
    assert red.pollq['T2TEST'] == 1007
    assert red.pollq['T2HUB1'] == 500
    # deleted servers are not polled, servers gone from the portal are removed
    assert 'T2OLD' not in red.pollq
    assert 'T2OLD' in red.servers
    assert 'T2GONE' not in red.pollq
    assert 'T2GONE' not in red.servers


def test_address_map(manager, red):
    manager.process_config_json(portal_json())
    assert red.address_map == {
        '10.0.0.1': 'T2TEST', '2001:db8::1': 'T2TEST',
        '10.0.0.9': 'T2OLD', '10.0.0.5': 'T2HUB1', '10.0.0.7': 'T2FIRE',
    }


def test_empty_config(manager, red):
    red.servers = {'T2X': {'id': 'T2X'}}
    red.pollq = {'T2X': 1}
    manager.process_config_json({})
    assert red.servers == {} and red.pollq == {}
    assert red.address_map == {}
