"""Точечная проверка адреса, группы v2fly и покрытия ASN без сети."""

from __future__ import annotations

import ipaddress as ipa
from argparse import Namespace
from pathlib import Path
from urllib.error import HTTPError

import pytest

import ru_asn_discovery as scan


def test_prefix_index_matches_collapsed_ranges():
    index = scan.PrefixIndex([
        ipa.ip_network('203.0.113.0/25'),
        ipa.ip_network('203.0.113.128/25'),
        ipa.ip_network('198.51.100.0/24'),
    ])

    assert len(index.nets) == 2
    assert index.lookup('203.0.113.0') == ipa.ip_network('203.0.113.0/24')
    assert index.lookup('203.0.113.255') == ipa.ip_network('203.0.113.0/24')
    assert '198.51.100.10' in index
    assert '198.51.100.255' in index
    assert '192.0.2.1' not in index
    assert index.lookup('192.0.2.1') is None


def test_parse_networks_skips_comments_and_ipv6():
    nets = scan.parse_networks('  # note\n203.0.113.0/24\n\n2001:db8::/32\n')
    assert nets == [ipa.ip_network('203.0.113.0/24')]


def test_parse_target_accepts_host_and_ipv4():
    assert scan.parse_target('Gismeteo.RU.') == ('domain', 'gismeteo.ru')
    assert scan.parse_target('203.0.113.10') == ('ip', '203.0.113.10')
    with pytest.raises(ValueError):
        scan.parse_target('2001:db8::1')
    with pytest.raises(ValueError):
        scan.parse_target('not a host')


def test_probe_checks_group_and_address_against_both_lists(monkeypatch, tmp_path, capsys):
    pages = {
        'gismeteo': '\n'.join([
            'gismeteo.ru',
            'full:gismeteo.com',
            'kept.example @ads',
            'regexp:^skip',
            'keyword:skip',
            '# comment',
        ]),
    }

    def fake_fetch(url):
        if url.endswith('/gismeteo'):
            return pages['gismeteo']
        if 'ipdeny.com' in url:
            return '203.0.113.0/24\n# comment\n'
        if 'country-ip-blocks' in url:
            return '203.0.113.0/25\n198.51.100.0/24\n'
        raise AssertionError(url)

    resolved = {
        'gismeteo.ru': ['203.0.113.10'],
        'gismeteo.com': ['198.51.100.10', '192.0.2.10'],
        'kept.example': ['203.0.113.20'],
        'extra.example': ['192.0.2.20'],
    }
    ripe_calls = []

    monkeypatch.setattr(scan, 'fetch', fake_fetch)
    monkeypatch.setattr(scan, 'get_addrs', lambda domain: (domain, resolved[domain]))
    monkeypatch.setattr(scan.time, 'sleep', lambda _seconds: None)

    def fake_ripe(ip, cache):
        ripe_calls.append(ip)
        cache[ip] = {'prefix': '192.0.2.0/24', 'asns': ['64500']}
        return cache[ip]

    monkeypatch.setattr(scan, 'ripe_lookup', fake_ripe)
    args = Namespace(
        check=['extra.example', '203.0.113.200', 'Gismeteo.RU'],
        group=['gismeteo'],
        workers=4,
    )
    scan.probe(args, tmp_path, {}, tmp_path / 'ripe_cache.json')

    text = (tmp_path / 'probe.csv').read_text()
    assert 'missing_ips.csv' not in {path.name for path in tmp_path.iterdir()}
    assert 'gismeteo.ru,203.0.113.10,203.0.113.0/24,203.0.113.0/25,in,' in text
    assert 'gismeteo.com,198.51.100.10,,198.51.100.0/24,in,' in text
    assert 'gismeteo.com,192.0.2.10,,,out,AS64500,192.0.2.0/24,gismeteo' in text
    assert 'extra.example,192.0.2.20,,,out,AS64500,192.0.2.0/24,cli' in text
    assert ',203.0.113.200,203.0.113.0/24,,in,' in text
    assert 'kept.example' in text
    assert '203.0.113.10' not in ripe_calls
    assert sorted(ripe_calls) == ['192.0.2.10', '192.0.2.20', '198.51.100.10', '203.0.113.200']
    out = capsys.readouterr().out
    assert 'split=ru' in out
    assert 'split=other' in out
    assert 'Outside the split union: 2' in out
    assert 'In only one list: 2' in out


def test_covered_counts_full_partial_and_holes():
    index = scan.PrefixIndex([
        ipa.ip_network('203.0.113.0/25'),
        ipa.ip_network('203.0.113.192/26'),
    ])

    assert index.covered(ipa.ip_network('203.0.113.0/25')) == 128
    assert index.covered(ipa.ip_network('203.0.113.128/26')) == 0
    assert index.covered(ipa.ip_network('203.0.113.0/24')) == 192
    assert scan.coverage_kind(192, 256) == 'partial'
    assert scan.coverage_kind(128, 128) == 'full'
    assert scan.coverage_kind(0, 256) == 'out'


def test_parse_asn_accepts_prefix_and_bare_number():
    assert scan.parse_asn('as48018') == '48018'
    assert scan.parse_asn('AS48018') == '48018'
    assert scan.parse_asn('48018') == '48018'
    with pytest.raises(ValueError):
        scan.parse_asn('AS')


def test_format_asn_marks_partial_country_and_full_geo():
    indexes = {
        'country': scan.PrefixIndex([ipa.ip_network('203.0.113.0/25')]),
        'geo': scan.PrefixIndex([
            ipa.ip_network('203.0.113.0/24'),
            ipa.ip_network('198.51.100.0/24'),
        ]),
    }
    prefixes = [
        ipa.ip_network('203.0.113.0/24'),
        ipa.ip_network('203.0.113.0/25'),
        ipa.ip_network('198.51.100.0/24'),
    ]

    lines, rows = scan.format_asn('48018', prefixes, indexes)
    text = '\n'.join(lines)

    assert 'AS48018  3 IPv4 prefixes in as-ip-blocks, 512 addresses' in text
    assert any(line.split()[:2] == ['country', 'partial'] and '128/512' in line for line in lines)
    assert any(line.split()[:2] == ['geo', 'full'] and '512/512' in line for line in lines)
    assert '203.0.113.0/24  country=partial 128/256  geo=full' in text
    assert '198.51.100.0/24  country=out  geo=full' in text
    assert rows[0][2:5] == ('partial', 128, 256)
    assert rows[2][5] == 'full'


def test_report_asns_writes_coverage_csv(monkeypatch, tmp_path, capsys):
    def fake_fetch(url):
        if 'country-ip-blocks' in url:
            return '203.0.113.0/25\n'
        if 'geo-ip-blocks' in url:
            return '203.0.113.0/24\n'
        if url.endswith('/as/48018/ipv4-aggregated.txt'):
            return '# AS48018\n203.0.113.0/24\n2001:db8::/32\n'
        raise AssertionError(url)

    monkeypatch.setattr(scan, 'fetch', fake_fetch)
    scan.report_asns(Namespace(asn=['AS48018']), tmp_path)

    text = (tmp_path / 'asn_coverage.csv').read_text()
    assert 'AS48018,203.0.113.0/24,partial,128,256,full,256,256' in text
    assert 'country partial' in capsys.readouterr().out


def test_report_asns_missing_list_is_empty(monkeypatch, tmp_path, capsys):
    def fake_fetch(url):
        if 'country-ip-blocks' in url or 'geo-ip-blocks' in url:
            return '203.0.113.0/24\n'
        raise HTTPError(url, 404, 'Not Found', hdrs=None, fp=None)

    monkeypatch.setattr(scan, 'fetch', fake_fetch)
    scan.report_asns(Namespace(asn=['AS48018']), tmp_path)

    assert 'AS48018  no IPv4 in as-ip-blocks' in capsys.readouterr().out
    assert (tmp_path / 'asn_coverage.csv').read_text().count('\n') == 1
