#!/usr/bin/env python3
"""Find BGP prefixes behind explicitly listed Russian V2Fly services missing in ipverse RU.

--check HOST and --group NAME test one address or one V2Fly list against the
ipdeny and ipverse files the RU split actually loads. Membership is a union,
the same combine as the entry policy. --asn AS12345 compares that ASN's
as-ip-blocks IPv4 with country-ip-blocks and geo-ip-blocks, fully or in part.
Discovery/reporting only; never modifies nftables.

Python 3.10+, standard library only.
"""
import argparse
import concurrent.futures as cf
import csv
import ipaddress as ipa
import json
import socket
import sys
import time
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

V2 = 'https://raw.githubusercontent.com/v2fly/domain-list-community/master/data/'
COUNTRY = 'https://raw.githubusercontent.com/ipverse/country-ip-blocks/master/country/ru/ipv4-aggregated.txt'
RU = 'https://raw.githubusercontent.com/ipverse/geo-ip-blocks/master/country/ru/ru-ipv4.txt'
# Same files as policy/entry.ru.example.yaml. Union of the two is the split.
SPLIT_SOURCES = (
    ('ipdeny', 'https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone'),
    ('ipverse', COUNTRY),
)
# country is the split's ipverse file. geo is the list the full scan treats as RU.
ASN_LISTS = (
    ('country', COUNTRY),
    ('geo', RU),
)
RIPE = 'https://stat.ripe.net/data/network-info/data.json?resource='
AS_BLOCKS = 'https://raw.githubusercontent.com/ipverse/as-ip-blocks/master/as/'
SKIP = {'tld-ru', 'tld-ru.com', 'tld-ru.net'}


def fetch(url):
    req = Request(url, headers={'User-Agent': 'ru-asn-discovery/1.0'})
    with urlopen(req, timeout=25) as r:
        return r.read().decode('utf-8')


class PrefixIndex:
    """Collapsed IPv4 networks with O(log n) membership."""

    def __init__(self, networks):
        self.nets = list(ipa.collapse_addresses(networks))
        self.starts = [int(net.network_address) for net in self.nets]

    def lookup(self, ip):
        addr = ipa.ip_address(ip)
        if addr.version != 4:
            return None
        index = bisect_right(self.starts, int(addr)) - 1
        if index >= 0 and addr in self.nets[index]:
            return self.nets[index]
        return None

    def __contains__(self, ip):
        return self.lookup(ip) is not None

    def covered(self, net):
        """How many addresses of an IPv4 network sit inside this index."""
        if net.version != 4:
            return 0
        start = int(net.network_address)
        end = int(net.broadcast_address)
        index = bisect_right(self.starts, start) - 1
        if index < 0:
            index = 0
        covered = 0
        while index < len(self.nets):
            other_start = self.starts[index]
            if other_start > end:
                break
            other_end = int(self.nets[index].broadcast_address)
            lo = start if start > other_start else other_start
            hi = end if end < other_end else other_end
            if lo <= hi:
                covered += hi - lo + 1
            index += 1
        return covered


def parse_networks(text):
    networks = []
    for line in text.splitlines():
        item = line.split('#', 1)[0].strip()
        if not item:
            continue
        net = ipa.ip_network(item, strict=False)
        if net.version == 4:
            networks.append(net)
    return networks


def parse_target(value):
    """Return ('domain', host) or ('ip', ipv4). Rejects IPv6 and junk."""
    text = value.strip()
    if not text:
        raise ValueError('empty target')
    try:
        addr = ipa.ip_address(text)
    except ValueError:
        domain = text.strip('.').lower()
        if (
            not domain
            or '*' in domain
            or '.' not in domain
            or not all(c.isalnum() or c in '.-' for c in domain)
        ):
            raise ValueError(f'not a domain or IPv4: {value}')
        return 'domain', domain
    if addr.version != 4:
        raise ValueError(f'IPv6 is outside the IPv4 split: {value}')
    return 'ip', str(addr)


def load_domains(name, cache, visited, domains, source, root=None):
    if name in visited:
        return
    visited.add(name)
    if name in SKIP or name.startswith('tld-'):
        return
    try:
        content = fetch(V2 + quote(name, safe=''))
    except (HTTPError, URLError, TimeoutError) as e:
        print(f'WARN: cannot load V2Fly {name}: {e}', file=sys.stderr)
        return
    for line in content.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        token = line.split()[0]
        if token.startswith('include:'):
            child = token.removeprefix('include:')
            load_domains(child, cache, visited, domains, source, child if name == 'category-ru' else root)
            continue
        # Only exact/full/domain syntax; ignore regexp, keyword, and exclusions.
        if token.startswith(('regexp:', 'keyword:')):
            continue
        domain = token.split(':', 1)[1] if token.startswith(('full:', 'domain:')) else token
        domain = domain.strip('.').lower()
        if '*' in domain or not all(c.isalnum() or c in '.-' for c in domain) or '.' not in domain:
            continue
        # Collapse to exact explicitly named host, do not guess wildcard subdomains.
        domains.add(domain)
        source[domain].add(root or name)


def get_addrs(domain):
    try:
        return domain, sorted({a[4][0] for a in socket.getaddrinfo(domain, 443, family=socket.AF_INET,
                           type=socket.SOCK_STREAM)})
    except (socket.gaierror, OSError):
        return domain, []


def ripe_lookup(ip, cache):
    if ip in cache:
        return cache[ip]
    try:
        obj = json.loads(fetch(RIPE + quote(ip) + '&sourceapp=ru-asn-discovery'))
        d = obj.get('data') or {}
        prefix = d.get('prefix')
        asns = [str(a).removeprefix('AS') for a in (d.get('asns') or [])]
        if prefix:
            p = ipa.ip_network(prefix, strict=False)
            if p.version != 4 or ipa.ip_address(ip) not in p:
                raise ValueError('Bad BGP prefix')
            val = {'prefix': str(p), 'asns': asns}
        else:
            val = {'prefix': '', 'asns': []}
    except (ValueError, HTTPError, URLError, TimeoutError) as e:
        print(f'WARN: RIPE {ip}: {e}', file=sys.stderr)
        val = {'prefix': '', 'asns': []}
    cache[ip] = val
    return val


def fill_ripe(ips, cache, cachefile):
    """Look up missing RIPEstat rows serially. Cached hits do not sleep."""
    print(
        f'RIPEstat for {len(ips)} IPs (cached: {sum(ip in cache for ip in ips)})',
        flush=True,
    )
    for idx, ip in enumerate(ips, 1):
        if ip not in cache:
            ripe_lookup(ip, cache)
            time.sleep(0.12)
        if idx % 100 == 0:
            cachefile.write_text(json.dumps(cache, indent=2))
            print(f'RIPE: {idx}/{len(ips)}', flush=True)
    if ips:
        cachefile.write_text(json.dumps(cache, indent=2))


def describe(ip, indexes, cache, sources):
    """One resolved address: which split list covers it, and RIPE if we have it."""
    hits = {name: indexes[name].lookup(ip) for name, _url in SPLIT_SOURCES}
    union = any(net is not None for net in hits.values())
    item = cache.get(ip) or {}
    asns = [f'AS{asn}' for asn in item.get('asns') or []]
    prefix = item.get('prefix') or ''
    bits = [ip]
    for name, _url in SPLIT_SOURCES:
        net = hits[name]
        bits.append(f'{name}={net if net is not None else "out"}')
    bits.append('split=' + ('ru' if union else 'other'))
    if asns:
        bits.append(','.join(asns))
    if prefix:
        bits.append(prefix)
    row = (
        ip,
        '' if hits['ipdeny'] is None else str(hits['ipdeny']),
        '' if hits['ipverse'] is None else str(hits['ipverse']),
        'in' if union else 'out',
        '|'.join(asns),
        prefix,
        sources,
    )
    return row, '  ' + '  '.join(bits), 0 if union else 1


def probe(args, out, cache, cachefile):
    """Test explicit hosts and/or V2Fly lists. Does not rewrite the discovery CSVs."""
    domains, sources = set(), defaultdict(set)
    literal = []
    visited = set()
    for group in args.group or []:
        load_domains(group, {}, visited, domains, sources)
    for raw in args.check or []:
        try:
            kind, value = parse_target(raw)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if kind == 'ip':
            literal.append(value)
        else:
            domains.add(value)
            sources[value].add('cli')
    if not domains and not literal:
        raise SystemExit('nothing to test: empty groups and no usable --check targets')

    print('Loading ipdeny and ipverse (split is their union)...', flush=True)
    indexes = {}
    for name, url in SPLIT_SOURCES:
        indexes[name] = PrefixIndex(parse_networks(fetch(url)))
        print(f'  {name}: {len(indexes[name].nets)} prefixes', flush=True)

    ordered = sorted(domains)
    print(f'Probe domains: {len(ordered)}; literal IPv4: {len(literal)}', flush=True)
    workers = max(1, min(args.workers, 32))
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        resolved = dict(pool.map(get_addrs, ordered)) if ordered else {}

    need = sorted({
        ip
        for addrs in resolved.values()
        for ip in addrs
        if any(ip not in indexes[name] for name, _url in SPLIT_SOURCES)
    } | {
        ip for ip in literal if any(ip not in indexes[name] for name, _url in SPLIT_SOURCES)
    })
    fill_ripe(need, cache, cachefile)

    rows = []
    outside = 0
    partial = 0
    for dom in ordered:
        src = '|'.join(sorted(sources[dom]))
        print(f'\n{dom}  [{src or "-"}]', flush=True)
        addrs = resolved[dom]
        if not addrs:
            print('  no IPv4', flush=True)
            rows.append((dom, '', '', '', '', '', '', src))
            continue
        for ip in addrs:
            row, line, missed = describe(ip, indexes, cache, src)
            print(line, flush=True)
            rows.append((dom, *row))
            outside += missed
            if row[3] == 'in' and (not row[1] or not row[2]):
                partial += 1
    if literal:
        print('\nliteral IPv4', flush=True)
        for ip in literal:
            row, line, missed = describe(ip, indexes, cache, '')
            print(line, flush=True)
            rows.append(('', *row))
            outside += missed
            if row[3] == 'in' and (not row[1] or not row[2]):
                partial += 1

    with (out / 'probe.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow((
            'domain', 'ip', 'ipdeny', 'ipverse', 'union', 'asn', 'bgp_prefix', 'v2fly_sources',
        ))
        writer.writerows(rows)
    print(f'\nOutside the split union: {outside}. In only one list: {partial}.', flush=True)
    print(f'Probe report: {(out / "probe.csv").resolve()}', flush=True)
    print('Discovery CSVs were left as they were.')


def parse_asn(value):
    """Return a decimal ASN without the AS prefix."""
    text = value.strip().upper()
    if text.startswith('AS'):
        text = text[2:]
    if not text.isdigit():
        raise ValueError(f'not an ASN: {value}')
    number = int(text)
    if not 1 <= number <= 4_294_967_295:
        raise ValueError(f'not an ASN: {value}')
    return str(number)


def coverage_kind(covered, total):
    if covered <= 0 or total <= 0:
        return 'out'
    if covered >= total:
        return 'full'
    return 'partial'


def as_blocks_url(asn):
    return f'{AS_BLOCKS}{asn}/ipv4-aggregated.txt'


def format_asn(asn, prefixes, indexes):
    """Lines and CSV rows: full, partial, or out for each list."""
    names = list(indexes)
    if not prefixes:
        return [f'AS{asn}  no IPv4 in as-ip-blocks'], []
    collapsed = list(ipa.collapse_addresses(prefixes))
    total = sum(net.num_addresses for net in collapsed)
    lines = [f'AS{asn}  {len(prefixes)} IPv4 prefixes in as-ip-blocks, {total} addresses']
    for name in names:
        index = indexes[name]
        covered = sum(index.covered(net) for net in collapsed)
        kind = coverage_kind(covered, total)
        counts = {'full': 0, 'partial': 0, 'out': 0}
        for net in prefixes:
            counts[coverage_kind(index.covered(net), net.num_addresses)] += 1
        lines.append(
            f'  {name:<7} {kind:<7} {covered}/{total} addresses'
            f'  prefixes full={counts["full"]} partial={counts["partial"]} out={counts["out"]}'
        )
    rows = []
    for net in prefixes:
        bits = [str(net)]
        row = [f'AS{asn}', str(net)]
        for name in names:
            covered = indexes[name].covered(net)
            kind = coverage_kind(covered, net.num_addresses)
            row.extend((kind, covered, net.num_addresses))
            if kind == 'partial':
                bits.append(f'{name}=partial {covered}/{net.num_addresses}')
            else:
                bits.append(f'{name}={kind}')
        lines.append('  ' + '  '.join(bits))
        rows.append(tuple(row))
    return lines, rows


def report_asns(args, out):
    numbers = []
    for raw in args.asn or []:
        try:
            numbers.append(parse_asn(raw))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    print('Loading country-ip-blocks and geo-ip-blocks...', flush=True)
    indexes = {}
    for name, url in ASN_LISTS:
        indexes[name] = PrefixIndex(parse_networks(fetch(url)))
        print(f'  {name}: {len(indexes[name].nets)} prefixes', flush=True)
    rows = []
    for asn in numbers:
        try:
            prefixes = parse_networks(fetch(as_blocks_url(asn)))
        except HTTPError as exc:
            if exc.code != 404:
                print(f'WARN: as-ip-blocks AS{asn}: {exc}', file=sys.stderr)
                continue
            prefixes = []
        except (ValueError, URLError, TimeoutError) as exc:
            print(f'WARN: as-ip-blocks AS{asn}: {exc}', file=sys.stderr)
            continue
        lines, asn_rows = format_asn(asn, prefixes, indexes)
        print(flush=True)
        for line in lines:
            print(line, flush=True)
        rows.extend(asn_rows)
    names = [name for name, _url in ASN_LISTS]
    header = ['asn', 'prefix']
    for name in names:
        header.extend((name, f'{name}_covered', f'{name}_addresses'))
    with (out / 'asn_coverage.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f'\nASN report: {(out / "asn_coverage.csv").resolve()}', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--limit', type=int, default=300, help='Max explicit domains (0 = all); default 300')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out', default='ru-asn-report')
    ap.add_argument('--refresh', action='store_true', help='Discard stored RIPE results')
    ap.add_argument(
        '--check', action='append', metavar='HOST',
        help='Domain or IPv4 to test against ipdeny and ipverse (repeatable)',
    )
    ap.add_argument(
        '--group', action='append', metavar='NAME',
        help='V2Fly list to test, for example gismeteo (repeatable)',
    )
    ap.add_argument(
        '--asn', action='append', metavar='ASN',
        help='ASN whose as-ip-blocks IPv4 is compared with country and geo (repeatable)',
    )
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cachefile = out / 'ripe_cache.json'
    cache = {} if args.refresh or not cachefile.exists() else json.loads(cachefile.read_text())
    if args.asn:
        report_asns(args, out)
    if args.check or args.group:
        probe(args, out, cache, cachefile)
        return
    if args.asn:
        return

    print('Loading RU zone and V2Fly lists...', flush=True)
    ru = PrefixIndex(parse_networks(fetch(RU)))

    domains, sources = set(), defaultdict(set)
    load_domains('category-ru', {}, set(), domains, sources)
    ordered = sorted(domains)
    if args.limit > 0 and len(ordered) > args.limit:
        # Sample evenly across the alphabetically sorted collection.
        ordered = [ordered[i * len(ordered) // args.limit] for i in range(args.limit)]
    print(f'Domains: {len(ordered)} (total explicitly listed: {len(domains)})', flush=True)

    with cf.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 32))) as pool:
        resolved = dict(pool.map(get_addrs, ordered))
    missing = sorted({ip for addrs in resolved.values() for ip in addrs if ip not in ru})
    print(f'IPs outside RU: {len(missing)}', flush=True)
    # Keep RIPE requests serial to avoid bursts / throttling; cached on next run.
    fill_ripe(missing, cache, cachefile)

    evidence = defaultdict(lambda: {'domains': set(), 'groups': set(), 'ips': set(), 'prefixes': set()})
    rows = []
    for dom in ordered:
        for ip in resolved[dom]:
            if ip in ru:
                continue
            item = cache.get(ip, {})
            prefix = item.get('prefix') or ''
            for asn in item.get('asns', []):
                key = f'AS{asn}'
                e = evidence[key]
                e['domains'].add(dom)
                e['groups'].update(sources[dom])
                e['ips'].add(ip)
                if prefix:
                    e['prefixes'].add(prefix)
                rows.append((dom, ip, key, prefix, '|'.join(sorted(sources[dom]))))
    with (out / 'missing_ips.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('domain', 'ip', 'asn', 'bgp_prefix', 'v2fly_sources'))
        w.writerows(sorted(rows))
    ranked = sorted(evidence.items(), key=lambda kv: (-len(kv[1]['groups']), -len(kv[1]['domains']), kv[0]))
    with (out / 'asn_candidates.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(('asn', 'distinct_v2fly_groups', 'domains', 'observed_ips', 'bgp_prefixes', 'examples'))
        for asn, e in ranked:
            w.writerow((asn, len(e['groups']), len(e['domains']), len(e['ips']), len(e['prefixes']),
                        ' '.join(sorted(e['domains'])[:8])))
    # BGP prefixes are candidates, not vetted RU prefixes. Never auto-install.
    candidates = set()
    for _, e in ranked:
        candidates.update(e['prefixes'])
    (out / 'prefixes_REVIEW_ONLY.txt').write_text('\n'.join(sorted(candidates, key=lambda p: (int(ipa.ip_network(p).network_address), ipa.ip_network(p).prefixlen))) + '\n')
    print('\nTop candidate ASNs (NOT an allowlist):')
    for asn, e in ranked[:20]:
        print(f'{asn:>10} groups={len(e["groups"]):3} domains={len(e["domains"]):4} ips={len(e["ips"]):4} prefix={len(e["prefixes"]):3}  {", ".join(sorted(e["domains"])[:3])}')
    print(f'\nReports: {out.resolve()}')
    print('IMPORTANT: do not import prefixes_REVIEW_ONLY.txt into nftables without reviewing CDN/shared-hosting ASNs.')


if __name__ == '__main__':
    main()
