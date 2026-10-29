"""
Tests for the DNS driver's record push paths: BIND dynamic updates, the
cloudflare client, the zone override and failure reporting.
"""

import socket

import pytest

import dns.rdatatype
import dns.rcode

HEARTBEAT = '_aprs2_heartbeat.aprs2.net'


def rrset_names(update):
    return [str(r.name) for r in update.update]


# ---- BIND ----

def test_bind_txt_push(driver, fake_bind):
    assert driver.dns_push_txt(HEARTBEAT, 'iid=i1 t=1234') is True
    assert len(fake_bind.sent) == 1
    update = fake_bind.sent[0]
    assert str(update.question[0].name) == 'aprs2.net.'

    # old records are deleted first, then the TXT is added with the short TTL
    assert update.update[0].rdtype == dns.rdatatype.ANY
    assert str(update.update[0].name) == HEARTBEAT + '.'
    txt = fake_bind.rrsets(dns.rdatatype.TXT)
    assert len(txt) == 1
    assert str(txt[0].name) == HEARTBEAT + '.'
    assert txt[0].ttl == 60
    # the value with its spaces is one character string
    assert [b''.join(r.strings) for r in txt[0]] == [b'iid=i1 t=1234']


def test_bind_txt_quoting(driver, fake_bind):
    driver.dns_push_txt(HEARTBEAT, 'say "hi" \\ there')
    txt = fake_bind.rrsets(dns.rdatatype.TXT)
    assert [b''.join(r.strings) for r in txt[0]] == [b'say "hi" \\ there']


def test_bind_address_push_uses_default_ttl(driver, fake_bind):
    assert driver.dns_push('x', 'rotate.aprs2.net', ['1.2.3.4'], ['2001:db8::1']) is True
    a = fake_bind.rrsets(dns.rdatatype.A)
    aaaa = fake_bind.rrsets(dns.rdatatype.AAAA)
    assert a[0].ttl == 600 and aaaa[0].ttl == 600
    assert [r.address for r in a[0]] == ['1.2.3.4']
    assert [r.address for r in aaaa[0]] == ['2001:db8::1']


def test_bind_cname_push(driver, fake_bind):
    driver.dns_push('x', 'host.aprs2.net', cname='rotate.aprs2.net')
    cname = fake_bind.rrsets(dns.rdatatype.CNAME)
    # names in an UPDATE are stored relative to the zone origin
    target = cname[0][0].target.derelativize(fake_bind.sent[0].origin)
    assert str(target) == 'rotate.aprs2.net.'
    assert fake_bind.rrsets(dns.rdatatype.A) == []


def test_unmanaged_zone_is_skipped(driver, fake_bind):
    assert driver.dns_push_txt('_x.example.org', 'v') is True
    assert driver.dns_push('x', 'host.example.org', ['1.1.1.1']) is True
    assert fake_bind.sent == []
    assert driver.dns_push_failures == 0


def test_unchanged_records_are_not_pushed_again(driver, fake_bind):
    driver.dns_push('x', 'rotate.aprs2.net', ['1.1.1.1', '2.2.2.2'])
    # scoring order changes do not matter
    driver.dns_push('x', 'rotate.aprs2.net', ['2.2.2.2', '1.1.1.1'])
    assert len(fake_bind.sent) == 1
    driver.dns_push('x', 'rotate.aprs2.net', ['2.2.2.2'])
    assert len(fake_bind.sent) == 2


def test_heartbeat_is_never_deduplicated(driver, fake_bind):
    driver.dns_push_txt(HEARTBEAT, 'same')
    driver.dns_push_txt(HEARTBEAT, 'same')
    assert len(fake_bind.sent) == 2


# ---- failure reporting ----

def test_bind_connection_failure(driver, fake_bind):
    fake_bind.raise_exc = socket.error("down")
    assert driver.dns_push('x', 'rotate.aprs2.net', ['1.1.1.1']) is False
    assert driver.dns_push_txt(HEARTBEAT, 'v') is False
    assert 'rotate.aprs2.net' not in driver.dns_update_cache
    assert driver.dns_push_failures == 1
    assert driver.dns_push_attempts == 1


def test_failed_push_is_retried_next_time(driver, fake_bind):
    fake_bind.raise_exc = socket.error("down")
    driver.dns_push('x', 'rotate.aprs2.net', ['1.1.1.1'])
    fake_bind.raise_exc = None
    assert driver.dns_push('x', 'rotate.aprs2.net', ['1.1.1.1']) is True
    assert len(fake_bind.sent) == 1
    assert driver.dns_update_cache['rotate.aprs2.net'] == '1.1.1.1 '
    # and now it is cached
    driver.dns_push('x', 'rotate.aprs2.net', ['1.1.1.1'])
    assert len(fake_bind.sent) == 1


@pytest.mark.parametrize('rcode', [dns.rcode.REFUSED, dns.rcode.NOTAUTH, dns.rcode.SERVFAIL])
def test_bind_refused_update_is_a_failure(driver, fake_bind, rcode):
    fake_bind.rcode = rcode
    assert driver.dns_push('x', 'rotate.aprs2.net', ['2.2.2.2']) is False
    assert driver.dns_push_failures == 1
    assert 'rotate.aprs2.net' not in driver.dns_update_cache


def test_push_fails_if_either_backend_fails(driver, fake_bind):
    class FailingCF:
        def dns_push(self, *a, **kw):
            return False
    driver.dns_cloudflare = FailingCF()
    assert driver.dns_push('x', 'rotate.aprs2.net', ['3.3.3.3']) is False
    assert len(fake_bind.sent) == 1
    assert driver.dns_push_failures == 1
    assert 'rotate.aprs2.net' not in driver.dns_update_cache


# ---- zone override ----

class CapturingCF:
    def __init__(self):
        self.pushes = []

    def dns_push(self, logid, zone, fqdn, *args, **kw):
        self.pushes.append((zone, fqdn, kw.get('txt')))
        return True


def test_override_off_uses_real_zone(driver, fake_bind):
    driver.dns_cloudflare = CapturingCF()
    driver.dns_push_txt(HEARTBEAT, 'v1')
    assert str(fake_bind.sent[0].question[0].name) == 'aprs2.net.'
    assert rrset_names(fake_bind.sent[0])[0] == HEARTBEAT + '.'
    assert driver.dns_cloudflare.pushes == [('aprs2.net', HEARTBEAT, 'v1')]


def test_override_rewrites_zone_and_name_on_both_backends(driver, fake_bind):
    driver.dns_zone_override = 'aprs.is'
    driver.dns_cloudflare = CapturingCF()

    driver.dns_push_txt(HEARTBEAT, 'v2')
    assert str(fake_bind.sent[0].question[0].name) == 'aprs.is.'
    assert rrset_names(fake_bind.sent[0])[0] == '_aprs2_heartbeat.aprs.is.'
    assert driver.dns_cloudflare.pushes == [('aprs.is', '_aprs2_heartbeat.aprs.is', 'v2')]

    driver.dns_push('x', 'rotate.aprs2.net', ['5.6.7.8'])
    assert rrset_names(fake_bind.sent[1])[0] == 'rotate.aprs.is.'
    assert driver.dns_cloudflare.pushes[1][:2] == ('aprs.is', 'rotate.aprs.is')
    # the change cache is keyed on the real name
    assert 'rotate.aprs2.net' in driver.dns_update_cache


def test_override_leaves_unmanaged_names_alone(driver, fake_bind):
    driver.dns_zone_override = 'aprs.is'
    driver.dns_push_txt('_x.example.org', 'v')
    assert fake_bind.sent == []
    assert driver.dns_apply_zone_override(None, 'q.example.org') == (None, 'q.example.org')
    assert driver.dns_apply_zone_override('aprs2.net', 'a.b.aprs2.net') == ('aprs.is', 'a.b.aprs.is')


# ---- cloudflare ----

CF_NAME = '_aprs2_heartbeat.aprs.is'


def test_cf_creates_txt_record(cloudflare):
    assert cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='iid=i1 t=1', ttl=60) is True
    assert cloudflare.api.calls == [('post', {
        'name': '_aprs2_heartbeat', 'type': 'TXT', 'content': 'iid=i1 t=1',
        'comment': 'aprs2-dynamic', 'ttl': 60})]


def test_cf_replaces_txt_in_place(cloudflare):
    cloudflare.api.records = [{'id': 'r1', 'name': CF_NAME, 'type': 'TXT', 'content': '"iid=i1 t=1"'}]
    assert cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='iid=i1 t=2', ttl=60) is True
    assert [c[:2] for c in cloudflare.api.calls] == [('put', 'r1')]
    assert cloudflare.api.calls[0][2]['content'] == 'iid=i1 t=2'


def test_cf_same_txt_is_noop_even_if_api_quotes_it(cloudflare):
    cloudflare.api.records = [{'id': 'r1', 'name': CF_NAME, 'type': 'TXT', 'content': '"iid=i1 t=2"'}]
    assert cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='iid=i1 t=2', ttl=60) is True
    assert cloudflare.api.calls == []


def test_cf_txt_replaces_first_and_deletes_rest(cloudflare):
    cloudflare.api.records = [
        {'id': 'r1', 'name': CF_NAME, 'type': 'A', 'content': '1.1.1.1'},
        {'id': 'r2', 'name': CF_NAME, 'type': 'TXT', 'content': 'old'},
    ]
    cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='new', ttl=60)
    assert [c[:2] for c in cloudflare.api.calls] == [('put', 'r1'), ('delete', 'r2')]


def test_cf_txt_keeps_matching_and_deletes_stray(cloudflare):
    cloudflare.api.records = [
        {'id': 'r1', 'name': CF_NAME, 'type': 'A', 'content': '1.1.1.1'},
        {'id': 'r2', 'name': CF_NAME, 'type': 'TXT', 'content': 'same'},
    ]
    cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='same', ttl=60)
    assert cloudflare.api.calls == [('delete', 'r1')]


def test_cf_txt_with_other_data_rejected(cloudflare):
    with pytest.raises(ValueError):
        cloudflare.dns_push('hb', 'aprs.is', CF_NAME, v4_addrs=['1.1.1.1'], txt='x')


def test_cf_address_path_ignores_txt_records(cloudflare):
    cloudflare.api.records = [
        {'id': 'r1', 'name': 'rot.aprs.is', 'type': 'A', 'content': '1.1.1.1'},
        {'id': 'r2', 'name': 'rot.aprs.is', 'type': 'TXT', 'content': 'unrelated'},
    ]
    assert cloudflare.dns_push('x', 'aprs.is', 'rot.aprs.is', v4_addrs=['1.1.1.1']) is True
    assert cloudflare.api.calls == []


def test_cf_address_change(cloudflare):
    cloudflare.api.records = [{'id': 'r1', 'name': 'rot.aprs.is', 'type': 'A', 'content': '1.1.1.1'}]
    assert cloudflare.dns_push('x', 'aprs.is', 'rot.aprs.is', v4_addrs=['2.2.2.2', '1.1.1.1'], v6_addrs=['2001:db8::1']) is True
    kinds = sorted((c[0], c[-1]['type'], c[-1]['content']) for c in cloudflare.api.calls)
    assert kinds == [('post', 'A', '2.2.2.2'), ('post', 'AAAA', '2001:db8::1')]


def test_cf_api_errors_are_reported(cloudflare):
    cloudflare.api.records = [{'id': 'r1', 'name': CF_NAME, 'type': 'TXT', 'content': 'old'}]
    cloudflare.api.fail = {'put'}
    assert cloudflare.dns_push('hb', 'aprs.is', CF_NAME, txt='new', ttl=60) is False

    cloudflare.api.records = [{'id': 'r1', 'name': CF_NAME, 'type': 'A', 'content': '9.9.9.9'}]
    assert cloudflare.dns_push('x', 'aprs.is', CF_NAME, v4_addrs=['8.8.8.8']) is False

    cloudflare.api.fail = {'get'}
    assert cloudflare.dns_push('x', 'aprs.is', CF_NAME, v4_addrs=['8.8.8.8']) is False


def test_cf_unknown_zone_is_a_failure(cloudflare):
    assert cloudflare.dns_push('x', 'nope.example', 'a.nope.example', v4_addrs=['1.1.1.1']) is False
    assert cloudflare.api.calls == []
