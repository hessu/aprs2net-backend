"""
Shared fixtures for the DNS driver tests.

The driver lives in aprs2net-dns.py, which is not an importable module
name, so it is loaded from its path. A DNSDriver is built without running
its constructor, which would need a config file, redis and the network,
and only the attributes the tests exercise are set.
"""

import importlib.util
import logging
import os
import sys
import types

import pytest

POLLER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if POLLER_DIR not in sys.path:
    sys.path.insert(0, POLLER_DIR)

import dns.query
import dns.tsigkeyring


def load_driver_module():
    spec = importlib.util.spec_from_file_location('aprs2net_dns', os.path.join(POLLER_DIR, 'aprs2net-dns.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='session')
def driver_module():
    return load_driver_module()


class FakeResponse:
    """A DNS UPDATE response with the given rcode (NOERROR by default)."""
    def __init__(self, rcode=0):
        self.flags = 0x8400 | rcode
        self.ednsflags = 0


class FakeBind:
    """
    Stands in for dns.query.tcp: records the UPDATE messages sent, and
    answers with a configurable rcode, or raises.
    """
    def __init__(self):
        self.sent = []
        self.rcode = 0
        self.raise_exc = None

    def __call__(self, update, where, timeout=None):
        if self.raise_exc:
            raise self.raise_exc
        self.sent.append(update)
        return FakeResponse(self.rcode)

    def rrsets(self, rdtype):
        return [r for u in self.sent for r in u.update if r.rdtype == rdtype]


@pytest.fixture
def fake_bind(monkeypatch):
    fb = FakeBind()
    monkeypatch.setattr(dns.query, 'tcp', fb)
    return fb


@pytest.fixture
def driver(driver_module, fake_bind):
    """
    A DNSDriver managing aprs2.net on a BIND backend, with failover
    attributes at their defaults and no cloudflare backend.
    """
    d = driver_module.DNSDriver.__new__(driver_module.DNSDriver)
    d.log = logging.getLogger('test-driver')
    d.dns_master = '127.0.0.1'
    d.dns_keyring = dns.tsigkeyring.from_text({'k': 'c1SUtd8gpvEIsyu5844lYVQ1REHT9H9FzfuzLYbiFEU='})
    d.dns_ttl = 600
    d.dns_zones = ['aprs2.net']
    d.dns_zone_override = None
    d.dns_cloudflare = None
    d.dns_update_cache = {}
    d.dns_push_attempts = 0
    d.dns_push_failures = 0
    d.failover = None
    d.failover_record_ttl = 60
    d.failover_timeout = 900
    d.failover_was_active = None
    d.failover_last_round = None
    d.send_metrics = True
    return d


class FakeCFRecords:
    """A fake cloudflare dns_records API over an in-memory list."""
    def __init__(self):
        self.records = []
        self.calls = []
        self.fail = set()    # method names which should raise

    def _check(self, name):
        if name in self.fail:
            import CloudFlare
            raise CloudFlare.exceptions.CloudFlareAPIError(1000, 'simulated failure')

    def get(self, zone_id, params=None):
        self._check('get')
        return [r for r in self.records if r['name'] == params['name']]

    def put(self, zone_id, rid, data=None):
        self._check('put')
        self.calls.append(('put', rid, data))
        return data

    def post(self, zone_id, data=None):
        self._check('post')
        self.calls.append(('post', data))
        return data

    def delete(self, zone_id, rid):
        self._check('delete')
        self.calls.append(('delete', rid))


@pytest.fixture
def cloudflare():
    """An aprs2cf client over the fake API, knowing two zones."""
    from aprs2_cloudflare import aprs2cf
    cf = aprs2cf.__new__(aprs2cf)
    cf.log = logging.getLogger('test-cloudflare')
    cf.zones = ['aprs.is', 'aprs2.net']
    cf.zone_ids = {'aprs.is': 'zone-is', 'aprs2.net': 'zone-2'}
    cf.cf = types.SimpleNamespace(zones=types.SimpleNamespace(dns_records=FakeCFRecords()))
    cf.api = cf.cf.zones.dns_records
    return cf
