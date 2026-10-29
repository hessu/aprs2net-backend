"""
Tests for the server scoring in aprs2_score.py.
"""

import pytest

from aprs2_score import Score, dur_str, version_key, version_older


@pytest.mark.parametrize('older,newer', [
    ('2.1.10', '2.1.11'),
    ('2.1.9', '2.1.10'),
    ('2.0.14', '2.1.0'),
    ('1.9', '2.0'),
    ('2.1.11', '2.1.11.1'),
    ('2.1.11', '2.1.11-g6099cb1'),      # a git build is newer than its base release
    ('2.1.10-g6099cb1', '2.1.11'),
    ('3.15b08', '3.15b09'),
    ('3.15b08', '3.16'),
    ('4.3.0b01', '4.3.0b02'),
    ('2.1.11.1', '2.1.11b'),            # a number sorts before a word, without raising
])
def test_version_ordering(older, newer):
    assert version_older(older, newer)
    assert not version_older(newer, older)
    assert not version_older(older, older)


def test_version_key():
    assert version_key('2.1.11-g6099cb1') == ((0, 2), (0, 1), (0, 11), (1, '-'), (1, 'g'), (0, 6099), (1, 'cb'), (0, 1))
    assert version_key(2.1) == ((0, 2), (0, 1))


@pytest.mark.parametrize('seconds,expected', [
    (0, '0s'),
    (45, '45s'),
    (60, '1m'),
    (90, '1m30s'),
    (3600, '1h'),
    (3661, '1h1m1s'),
    (86400, '1d'),
    (129600, '1d12h'),
    (90061, '1d1h1m1s'),
])
def test_dur_str(seconds, expected):
    assert dur_str(seconds) == expected


def good_score():
    """A score with a fast HTTP status and fast APRS-IS polls on both families."""
    s = Score()
    s.http_status_t = 0.2
    s.poll_t_14580 = {'ipv4': 0.1, 'ipv6': 0.1}
    return s


def good_props():
    return {'worst_load': 10.0, 'uptime': 10 * 86400, 'uplinks': [{'up': 86400}],
            'soft': 'aprsc', 'vers': '2.1.19'}


def test_no_http_status_is_worst_score():
    s = Score()
    s.poll_t_14580 = {'ipv4': 0.1}
    assert s.get(good_props()) == s.score_max


def test_no_aprsis_port_is_worst_score():
    s = Score()
    s.http_status_t = 0.1
    assert s.get(good_props()) == s.score_max


def test_good_server_scores_only_its_load():
    s = good_score()
    assert s.get(good_props()) == 100.0
    assert s.score_components['user_load'] == [100.0, '10.0 %']
    assert s.score_components['http_rtt'][0] == 0
    assert s.score_components['aprsis_rtt'][0] == 0
    assert 'uptime' not in s.score_components
    assert 'uplink_uptime' not in s.score_components
    assert 'version' not in s.score_components


def test_rtt_above_good_enough_is_penalised():
    s = good_score()
    s.http_status_t = 2.6                      # 2.0 s over -> 100
    s.poll_t_14580 = {'ipv4': 1.6, 'ipv6': 0.6}  # 1.0 s over on v4 -> 40, averaged over 2 -> 20
    s.get(good_props())
    assert s.score_components['http_rtt'] == [100.0, '2.600 s']
    assert s.score_components['aprsis_rtt'] == [20.0, '1.100 s']


def test_aprsis_rtt_score_averages_families():
    s = Score()
    assert s.aprsis_rtt_score({'ipv4': 0.6, 'ipv6': 2.6}) == (40.0, 1.6)


def test_missing_load_counts_as_full():
    s = good_score()
    s.get({})
    assert s.score_components['user_load'] == [1000.0, '100.0 %']


@pytest.mark.parametrize('uptime,penalty', [(0, 500.0), (-5, 500.0), (900, 250.0), (1800, None), (86400, None)])
def test_recent_reboot_penalty(uptime, penalty):
    s = good_score()
    props = good_props()
    props['uptime'] = uptime
    s.get(props)
    if penalty is None:
        assert 'uptime' not in s.score_components
    else:
        assert s.score_components['uptime'][0] == penalty


@pytest.mark.parametrize('up,penalty', [(0, 900), (300, 600), (900, None), (5000, None)])
def test_recent_uplink_penalty(up, penalty):
    s = good_score()
    props = good_props()
    props['uplinks'] = [{'up': up}]
    s.get(props)
    if penalty is None:
        assert 'uplink_uptime' not in s.score_components
    else:
        assert s.score_components['uplink_uptime'][0] == penalty


def test_no_uplinks_gives_no_uplink_penalty():
    s = good_score()
    props = good_props()
    props['uplinks'] = []
    s.get(props)
    assert 'uplink_uptime' not in s.score_components


@pytest.mark.parametrize('version,penalty,disallowed', [
    ('2.1.19', None, False),
    ('2.1.11', None, False),
    ('2.1.10', 600, False),     # older than 2.1.11, but not older than 2.1.10
    ('2.1.5', 600, True),       # older than both
    ('2.0.14', 600, True),
])
def test_old_aprsc_version(version, penalty, disallowed):
    s = good_score()
    props = good_props()
    props['vers'] = version
    s.get(props)
    if penalty is None:
        assert 'version' not in s.score_components
    else:
        assert s.score_components['version'] == [penalty, version]
    assert s.server_version_disallowed(props) is disallowed


def test_unknown_software_has_no_version_rules():
    s = good_score()
    props = good_props()
    props['soft'] = 'javAPRSSrvr'
    props['vers'] = '3.15b08'
    s.get(props)
    assert 'version' not in s.score_components
    assert s.server_version_disallowed(props) is False


def test_fullfeed_score_replaces_port_specific_components():
    s = good_score()
    s.http_status_t = 2.6
    props = good_props()
    props['uptime'] = 0
    props['fullfeed_load'] = 50.0
    s.get(props)
    s.poll_t_fullfeed = {'ipv4': 1.6}
    ff = s.get_fullfeed(props)
    c = s.fullfeed_score_components
    assert c['http_rtt'] == s.score_components['http_rtt']
    assert c['uptime'] == s.score_components['uptime']
    assert c['aprsis_rtt'] == [40.0, '1.600 s']
    assert c['user_load'] == [500.0, '50.0 %']
    assert ff == 100.0 + 500.0 + 40.0 + 500.0
    # the main score is untouched
    assert s.score_components['user_load'] == [100.0, '10.0 %']


def test_fullfeed_score_none_without_poll():
    s = good_score()
    s.get(good_props())
    assert s.get_fullfeed(good_props()) is None


def test_explicit_failure_component_dominates():
    s = good_score()
    s.score_add('server-fail', 1000, '1000')
    assert s.get(good_props()) == 1100.0
