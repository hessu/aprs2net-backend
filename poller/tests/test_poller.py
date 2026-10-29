"""
Tests for the poller process in aprs2net-poller.py: how a poll result is
turned into server state and stored.
"""

import logging
import types

import pytest


class FakeRedis:
    def __init__(self):
        self.status = {}
        self.logs = []
        self.messages = []
        self.avail_calls = []

    def getServerStatus(self, id):
        return self.status.get(id)

    def setServerStatus(self, id, state):
        self.status[id] = state

    def storeServerLog(self, id, entry):
        self.logs.append((id, entry))

    def sendServerStatusMessage(self, msg):
        self.messages.append(msg)

    def updateAvail(self, id, seconds, is_up):
        self.avail_calls.append((id, seconds, is_up))
        return (99.0, 98.0)


class FakePoll:
    """Stands in for aprs2_poll.Poll with a scripted outcome."""
    outcome = True
    props = {}
    raise_exc = None

    def __init__(self, log, server, red, software_type_cache, rates_cache, address_map):
        self.log = log
        self.properties = dict(FakePoll.props)
        self.errors = []

    def poll(self):
        self.log.info("fake poll")
        if FakePoll.raise_exc:
            raise FakePoll.raise_exc
        if not FakePoll.outcome:
            self.errors.append(['web-http-fail', 'down'])
        return FakePoll.outcome

    def error(self, code, msg):
        self.errors.append([code, msg])
        return False


@pytest.fixture
def poller(poller_module, monkeypatch):
    monkeypatch.setattr(poller_module.aprs2_poll, 'Poll', FakePoll)
    FakePoll.outcome = True
    FakePoll.props = {'id': 'T2TEST', 'type': 'aprsc', 'soft': 'aprsc', 'vers': '2.1.19', 'os': 'Linux', 'score': 12.5}
    FakePoll.raise_exc = None

    metrics = []

    class FakeSender:
        def __init__(self, log, fqdn):
            self.fqdn = fqdn

        def send(self, metric, value):
            metrics.append((self.fqdn, metric, value))
    monkeypatch.setattr(poller_module.aprs2_graphite, 'GraphiteSender', FakeSender)

    now = [10000]
    monkeypatch.setattr(poller_module.time, 'time', lambda: now[0])

    p = poller_module.Poller.__new__(poller_module.Poller)
    p.log = logging.getLogger('test-poller')
    p.log_poller = logging.getLogger('test-poller.poll')
    p.red = FakeRedis()
    p.software_type_cache = {}
    p.rates_cache = {}
    p.address_map = {}
    p.poll_interval = 300
    p.metrics = metrics
    p.now = now
    return p


SERVER = {'id': 'T2TEST', 'ipv4': '10.0.0.1'}


def test_successful_poll_stores_state(poller):
    poller.perform_poll(SERVER)
    state = poller.red.status['T2TEST']
    assert state['status'] == 'ok'
    assert state['props']['score'] == 12.5
    assert state['errors'] == []
    assert state['last_test'] == 10000
    assert state['last_change'] == 10000
    assert poller.red.messages[0] == {'config': SERVER, 'status': state}
    assert poller.red.logs[0][0] == 'T2TEST'
    assert 'fake poll' in poller.red.logs[0][1]['log']
    assert ('server.T2TEST', 'ok', 1) in poller.metrics
    assert ('server.T2TEST', 'score', 12.5) in poller.metrics


def test_failed_poll_keeps_software_info_from_previous(poller):
    poller.perform_poll(SERVER)
    FakePoll.outcome = False
    FakePoll.props = {}
    poller.now[0] = 10300
    poller.perform_poll(SERVER)
    state = poller.red.status['T2TEST']
    assert state['status'] == 'fail'
    assert state['errors'] == [['web-http-fail', 'down']]
    assert state['props']['soft'] == 'aprsc' and state['props']['vers'] == '2.1.19'
    assert 'score' not in state['props']
    assert state['last_change'] == 10300
    assert ('server.T2TEST', 'ok', 0) in poller.metrics


def test_last_change_only_moves_on_status_change(poller):
    poller.perform_poll(SERVER)
    poller.now[0] = 10300
    poller.perform_poll(SERVER)
    assert poller.red.status['T2TEST']['last_change'] == 10000
    assert poller.red.status['T2TEST']['last_test'] == 10300


def test_availability_updated_between_regular_polls(poller):
    poller.perform_poll(SERVER)
    assert poller.red.avail_calls == []   # no previous test to measure from
    poller.now[0] = 10300
    poller.perform_poll(SERVER)
    assert poller.red.avail_calls == [('T2TEST', 300, True)]
    assert poller.red.status['T2TEST']['avail_3'] == 99.0
    # a gap of three intervals or more is not counted
    poller.now[0] = 10300 + 900
    poller.perform_poll(SERVER)
    assert len(poller.red.avail_calls) == 1


def test_availability_not_updated_when_out_of_service(poller):
    server = dict(SERVER, out_of_service=True)
    poller.perform_poll(server)
    poller.now[0] = 10300
    poller.perform_poll(server)
    assert poller.red.avail_calls == []


def test_crashing_poll_is_a_failure(poller):
    FakePoll.raise_exc = RuntimeError("boom")
    poller.perform_poll(SERVER)
    state = poller.red.status['T2TEST']
    assert state['status'] == 'fail'
    assert state['errors'][0][0] == 'crash'
    assert 'boom' in state['errors'][0][1]
