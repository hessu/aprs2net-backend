"""
Tests for the DNS driver's polling round: the failover heartbeat rule,
cache clearing on failover transitions, and the backup not sending metrics.
"""

import socket
import time
import types

import pytest


class FakeFailover:
    def __init__(self, active=True, role='primary'):
        self.active = active
        self.role = role
        self.heartbeats = []

    def this_is_active(self):
        return self.active

    def update_record(self, *, update_callback):
        self.heartbeats.append(update_callback)


@pytest.fixture
def round_driver(driver, monkeypatch):
    """
    A driver whose poll() round runs without pollers or redis: the status
    fetch and merge are stubbed, and update_dns is replaced per test.
    """
    driver.fetch_full_status = lambda: {'poller': {}}
    driver.red = types.SimpleNamespace(getServers=lambda: {}, sendDnsStatusMessage=lambda m: None)
    driver.merge_status = lambda servers, status_set: {}
    driver.update_dns = lambda servers, status: None
    driver.failover = FakeFailover()
    return driver


@pytest.fixture
def mono(driver_module, monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(driver_module.time, 'monotonic', lambda: now[0])

    def advance(seconds):
        now[0] += seconds
    return advance


def pushes(driver, names, fail=()):
    """An update_dns stand-in pushing one A record per name."""
    def update_dns(servers, status):
        driver.dns_update_cache = {}
        for i, n in enumerate(names):
            driver.dns_push('x', n + '.aprs2.net', ['10.0.0.%d' % i])
    return update_dns


class FlakyBind:
    """Fails the UPDATE for names starting with any of the given prefixes."""
    def __init__(self, fake_bind, fail_prefixes):
        self.fake_bind = fake_bind
        self.fail_prefixes = fail_prefixes

    def __call__(self, update, where, timeout=None):
        for r in update.update:
            if any(str(r.name).startswith(p) for p in self.fail_prefixes):
                raise socket.error("down")
        return self.fake_bind(update, where, timeout)


# ---- heartbeat after an update round ----

def test_heartbeat_written_after_clean_round(round_driver, driver_module):
    round_driver.update_dns = pushes(round_driver, ['a', 'b'])
    round_driver.poll()
    assert len(round_driver.failover.heartbeats) == 1
    assert round_driver.failover.heartbeats[0].__func__ is driver_module.DNSDriver.dns_push_txt


def test_heartbeat_written_when_nothing_to_push(round_driver):
    round_driver.poll()
    assert len(round_driver.failover.heartbeats) == 1


@pytest.mark.parametrize('names,failing,expect_heartbeat', [
    (['a'], ['a.'], False),
    (['a', 'b'], ['a.'], True),
    (['a', 'b', 'c'], ['a.', 'b.'], False),
    (['a', 'b', 'c', 'e'], ['a.', 'b.'], True),
    (['a', 'b', 'c', 'e'], ['a.', 'b.', 'c.'], False),
])
def test_heartbeat_needs_half_of_pushes_to_succeed(round_driver, fake_bind, monkeypatch, names, failing, expect_heartbeat):
    import dns.query
    monkeypatch.setattr(dns.query, 'tcp', FlakyBind(fake_bind, failing))
    round_driver.update_dns = pushes(round_driver, names)
    round_driver.poll()
    assert (len(round_driver.failover.heartbeats) == 1) is expect_heartbeat


def test_failure_counters_reset_each_round(round_driver, fake_bind):
    fake_bind.raise_exc = socket.error("down")
    round_driver.update_dns = pushes(round_driver, ['a'])
    round_driver.poll()
    assert round_driver.failover.heartbeats == []

    fake_bind.raise_exc = None
    round_driver.update_dns = lambda servers, status: None
    round_driver.poll()
    assert len(round_driver.failover.heartbeats) == 1


def test_passive_backup_does_not_update_or_heartbeat(round_driver):
    round_driver.failover = FakeFailover(active=False, role='backup')
    called = []
    round_driver.update_dns = lambda servers, status: called.append(1)
    round_driver.poll()
    assert called == []
    assert round_driver.failover.heartbeats == []


def test_without_failover_dns_is_always_updated(round_driver):
    round_driver.failover = None
    called = []
    round_driver.update_dns = lambda servers, status: called.append(1)
    round_driver.poll()
    assert called == [1]


def test_round_without_any_poller_status_does_nothing(round_driver):
    round_driver.fetch_full_status = lambda: {}
    called = []
    round_driver.update_dns = lambda servers, status: called.append(1)
    round_driver.poll()
    assert called == []
    assert round_driver.failover.heartbeats == []


# ---- cache clearing on failover transitions ----

def test_cache_cleared_on_transitions(round_driver, mono):
    fo = FakeFailover(active=False, role='backup')
    round_driver.failover = fo
    cache = {'rotate.aprs2.net': 'x'}

    round_driver.dns_update_cache = dict(cache)
    round_driver.poll()
    assert round_driver.dns_update_cache == {}     # first round starts clean

    round_driver.dns_update_cache = dict(cache)
    mono(60)
    round_driver.poll()
    assert round_driver.dns_update_cache == cache  # unchanged state keeps it

    fo.active = True
    mono(60)
    round_driver.poll()
    assert round_driver.dns_update_cache == {}     # passive -> active

    round_driver.dns_update_cache = dict(cache)
    mono(60)
    round_driver.poll()
    assert round_driver.dns_update_cache == cache

    fo.active = False
    mono(60)
    round_driver.poll()
    assert round_driver.dns_update_cache == {}     # active -> passive


def test_cache_cleared_after_long_gap_between_rounds(round_driver, mono):
    cache = {'rotate.aprs2.net': 'x'}
    round_driver.poll()

    round_driver.dns_update_cache = dict(cache)
    mono(round_driver.failover_timeout - 1)
    round_driver.poll()
    assert round_driver.dns_update_cache == cache

    round_driver.dns_update_cache = dict(cache)
    mono(round_driver.failover_timeout + 1)
    round_driver.poll()
    assert round_driver.dns_update_cache == {}


def test_records_repushed_once_after_transition(round_driver, fake_bind, mono):
    fo = FakeFailover(active=False, role='backup')
    round_driver.failover = fo
    round_driver.update_dns = lambda servers, status: round_driver.dns_push('x', 'rotate.aprs2.net', ['7.7.7.7'])

    round_driver.poll()
    assert fake_bind.sent == []
    fo.active = True
    mono(60)
    round_driver.poll()
    mono(60)
    round_driver.poll()
    assert len(fake_bind.sent) == 1


def test_no_failover_never_touches_cache(round_driver, mono):
    round_driver.failover = None
    round_driver.dns_update_cache = {'k': 'v'}
    mono(10000)
    assert round_driver.failover_this_is_active() is True
    assert round_driver.dns_update_cache == {'k': 'v'}


# ---- metrics ----

@pytest.fixture
def merge_driver(driver, driver_module, monkeypatch):
    built = []

    class FakeSender:
        def __init__(self, log, fqdn):
            built.append(fqdn)

        def send(self, metric, value):
            pass
    monkeypatch.setattr(driver_module.aprs2_graphite, 'GraphiteSender', FakeSender)

    driver.max_test_result_age = 10 ** 9
    driver.red = types.SimpleNamespace(
        getServerStatus=lambda i: None,
        updateAvail=lambda i, t, ok: (100.0, 100.0),
        setServerStatus=lambda i, m: None)
    driver.senders_built = built
    return driver


SERVERS = {'T2TEST': {'id': 'T2TEST', 'host': 'test', 'deleted': False, 'out_of_service': False}}


def status_set():
    return {'T2TEST': {'fi': {
        'id': 'T2TEST', 'status': 'ok', 'score': 1.0, 'last_test': time.time(),
        'props': {'clients': 1}, 'avail_3': 100, 'fullfeed_status': 'ok'}}}


def test_primary_sends_metrics(merge_driver):
    merge_driver.send_metrics = True
    merge_driver.merge_status(SERVERS, status_set())
    assert merge_driver.senders_built == ['server.T2TEST']


def test_backup_sends_no_metrics(merge_driver):
    merge_driver.send_metrics = False
    merged = merge_driver.merge_status(SERVERS, status_set())
    assert merge_driver.senders_built == []
    # the status is still merged for the local database and web UI
    assert merged['T2TEST']['status'] == 'ok'
