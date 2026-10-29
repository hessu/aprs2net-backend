"""
Tests for the primary/backup failover state machine in failover.py.
"""

import logging

import pytest

import failover
from failover import Failover

RECORD = '_aprs2_heartbeat.aprs2.net'
TIMEOUT = 900


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the staleness timer."""
    now = [1000.0]
    monkeypatch.setattr(failover.time, 'monotonic', lambda: now[0])

    def advance(seconds):
        now[0] += seconds
    return advance


def make(records, role='backup'):
    """
    A Failover instance whose DNS lookups return the given list of raw TXT
    strings. The list is read on every lookup, so tests can mutate it.
    None simulates a failed lookup.
    """
    f = Failover(logging.getLogger('test-failover'), RECORD, 'i-' + role, role, timeout=TIMEOUT)
    f._resolve = lambda: records[0]
    return f


def test_primary_is_always_active_and_writes_heartbeat():
    f = make([["iid=someone t=1"]], 'primary')
    assert f.this_is_active() is True

    written = []
    f.update_record(update_callback=lambda fqdn, value: written.append((fqdn, value)))
    assert len(written) == 1
    assert written[0][0] == RECORD
    assert written[0][1].startswith('iid=i-primary t=')


def test_backup_never_writes_heartbeat(clock):
    r = [["iid=p t=1"]]
    f = make(r)
    written = []
    f.update_record(update_callback=lambda fqdn, value: written.append(value))
    assert written == []

    # not even while covering
    f.this_is_active()
    clock(TIMEOUT)
    assert f.this_is_active() is True
    f.update_record(update_callback=lambda fqdn, value: written.append(value))
    assert written == []


def test_backup_passive_while_heartbeat_changes(clock):
    r = [["iid=p t=100"]]
    f = make(r)
    assert f.this_is_active() is False
    clock(600)
    r[0] = ["iid=p t=700"]
    assert f.this_is_active() is False
    clock(600)
    r[0] = ["iid=p t=1300"]
    assert f.this_is_active() is False


def test_backup_takes_over_after_timeout(clock):
    r = [["iid=p t=100"]]
    f = make(r)
    assert f.this_is_active() is False
    clock(TIMEOUT - 1)
    assert f.this_is_active() is False
    clock(1)
    assert f.this_is_active() is True
    clock(300)
    assert f.this_is_active() is True


def test_backup_goes_passive_when_primary_returns(clock):
    r = [["iid=p t=100"]]
    f = make(r)
    f.this_is_active()
    clock(TIMEOUT)
    assert f.this_is_active() is True
    r[0] = ["iid=p t=2000"]
    assert f.this_is_active() is False


def test_fresh_backup_waits_full_timeout_even_for_old_heartbeat(clock):
    # the t= value is not trusted; only observed change matters
    r = [["iid=p t=1"]]
    f = make(r)
    assert f.this_is_active() is False
    clock(TIMEOUT - 1)
    assert f.this_is_active() is False


@pytest.mark.parametrize('records', [[], ["junk"], ["iid=p"], ["t=5"], None])
def test_no_usable_record_keeps_passive(clock, records):
    f = make([records])
    assert f.this_is_active() is False
    clock(TIMEOUT * 2)
    assert f.this_is_active() is False


@pytest.mark.parametrize('records', [[], ["junk"], None])
def test_no_usable_record_keeps_covering(clock, records):
    r = [["iid=p t=1"]]
    f = make(r)
    f.this_is_active()
    clock(TIMEOUT)
    assert f.this_is_active() is True
    r[0] = records
    assert f.this_is_active() is True


def test_newest_record_is_watched_and_garbage_ignored(clock):
    r = [["junk", "iid=p t=5", "iid=p t=9"]]
    f = make(r)
    f.this_is_active()
    assert f.seen_value == "iid=p t=9"


def test_record_change_resets_staleness_timer(clock):
    r = [["iid=p t=1"]]
    f = make(r)
    f.this_is_active()
    clock(TIMEOUT - 10)
    r[0] = ["iid=p t=2"]
    assert f.this_is_active() is False
    clock(TIMEOUT - 10)
    assert f.this_is_active() is False
    clock(10)
    assert f.this_is_active() is True


def test_invalid_role_rejected():
    with pytest.raises(ValueError):
        Failover(logging.getLogger('x'), RECORD, 'i', 'master')


def test_txt_record_parsing():
    rec = failover.TxtRecord("iid=abc t=1700000000")
    assert rec.instance_id == 'abc'
    assert rec.timestamp == 1700000000
    with pytest.raises(ValueError):
        failover.TxtRecord("iid=abc")
    with pytest.raises(ValueError):
        failover.TxtRecord("t=notanumber iid=x")


def test_resolve_joins_txt_strings(monkeypatch):
    """The resolver's TXT rdata strings are joined into one value."""
    import dns.rdata, dns.rdataclass, dns.rdatatype, dns.resolver
    rd = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.TXT, '"iid=x t=9"')
    monkeypatch.setattr(dns.resolver, 'resolve', lambda name, rdtype, lifetime=None: [rd])
    f = Failover(logging.getLogger('x'), RECORD, 'i', 'backup')
    assert f._resolve() == ["iid=x t=9"]


def test_resolve_nxdomain_and_failure(monkeypatch):
    import dns.resolver, dns.exception

    def nx(name, rdtype, lifetime=None):
        raise dns.resolver.NXDOMAIN()
    monkeypatch.setattr(dns.resolver, 'resolve', nx)
    f = Failover(logging.getLogger('x'), RECORD, 'i', 'backup')
    assert f._resolve() == []

    def timeout(name, rdtype, lifetime=None):
        raise dns.exception.Timeout()
    monkeypatch.setattr(dns.resolver, 'resolve', timeout)
    assert f._resolve() is None
