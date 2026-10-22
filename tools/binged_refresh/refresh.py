"""Preserve a custom M3U; validate exact authorized URLs, never extrapolate sessions.

No browser credentials, PIN storage, proxy, or catalog-wide session substitution.
Reports contain channel identifiers and status, never playback URLs.
"""
import argparse
import collections
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

HOSTS = frozenset(('sofi.dulo.cc', '2.dulo.cx', 'loki.dulo.cc'))


@dataclasses.dataclass
class Entry:
    index: int
    line: int
    url: str
    name: str
    group: str
    epg_id: str
    source_id: str


def source_id(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or u.hostname not in HOSTS or u.port not in (None, 443):
        raise ValueError('unsupported_stream_host')
    m = re.fullmatch(r'/play/[^/]+/([A-Za-z0-9_-]+)\.m3u8', u.path)
    if not m or u.username or u.password:
        raise ValueError('unsupported_stream_path')
    return m[1]


def parse(text):
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].lstrip('\ufeff').startswith('#EXTM3U'):
        raise ValueError('missing_extm3u_header')
    entries, pending = [], None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('#EXTINF:'):
            if pending is not None:
                raise ValueError('entry_missing_url')
            # Commas inside quoted metadata are legal.
            m = re.match(r'#EXTINF:(?:[^,"\n]|"[^"]*")*,(.*)$', stripped)
            if not m:
                raise ValueError('invalid_extinf')
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', stripped[:m.start(1)]))
            pending = (m[1], attrs)
        elif stripped and not stripped.startswith('#'):
            if pending is None:
                raise ValueError('url_without_extinf')
            name, attrs = pending
            entries.append(Entry(len(entries), i, stripped, name,
                                 attrs.get('group-title', ''), attrs.get('tvg-id', ''), source_id(stripped)))
            pending = None
    if pending is not None or not entries:
        raise ValueError('incomplete_playlist')
    ids = [e.source_id for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_source_ids')
    return lines, entries


def public_object(path, variable):
    text = Path(path).read_text()
    start = text.index('=', text.index('window.' + variable)) + 1
    return json.JSONDecoder().raw_decode(text[start:].lstrip())[0]


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_resource(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_resource(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme != 'https' or u.hostname not in HOSTS or u.port not in (None, 443) or u.username or u.password:
        raise ValueError('resource_host_not_allowed')


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(SafeRedirect())

    def get(self, url, limit=2_000_000):
        validate_resource(url)
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'JOSH-IPTV-ALT-Validation/1.0'})
                with self.opener.open(req, timeout=15) as r:
                    data = r.read(limit + 1)
                    if len(data) > limit:
                        raise ValueError('resource_exceeds_limit')
                    return data, {'http': r.status, 'bytes': len(data)}
            except urllib.error.HTTPError as e:
                if attempt == 0 and e.code in (429, 502, 503, 504):
                    time.sleep(2)
                    continue
                return None, {'http': e.code, 'reason': 'http_error'}
            except (TimeoutError, OSError, ValueError):
                if attempt == 0:
                    time.sleep(1)
                    continue
                return None, {'reason': 'network_or_resource_error'}


def test_hls(url, client, depth=0):
    if depth > 3:
        return {'status': 'failed', 'reason': 'manifest_recursion_limit'}
    data, info = client.get(url)
    if data is None:
        return {'status': 'failed', **info}
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        return {'status': 'failed', 'reason': 'manifest_not_text'}
    if not text.lstrip().startswith('#EXTM3U'):
        return {'status': 'failed', 'reason': 'not_hls', **info}
    lines = text.splitlines()
    uris = [line.strip() for line in lines if line.strip() and not line.startswith('#')]
    if not uris:
        return {'status': 'failed', 'reason': 'empty_hls_manifest', **info}
    if '#EXT-X-STREAM-INF:' in text:
        result = test_hls(urllib.parse.urljoin(url, uris[0]), client, depth + 1)
        return {**result, 'master_manifest': True}
    keys = re.findall(r'#EXT-X-KEY:[^\r\n]*URI="([^"]+)"', text)
    if keys:
        key, key_info = client.get(urllib.parse.urljoin(url, keys[-1]), limit=65536)
        if not key:
            return {'status': 'failed', 'reason': 'key_unavailable', 'key_http': key_info.get('http')}
    init = re.findall(r'#EXT-X-MAP:[^\r\n]*URI="([^"]+)"', text)
    if init:
        data, init_info = client.get(urllib.parse.urljoin(url, init[-1]), limit=10_000_000)
        if not data:
            return {'status': 'failed', 'reason': 'initialization_unavailable', 'init_http': init_info.get('http')}
    # Choose a recent complete segment, not the oldest edge of a moving window.
    segment, seg_info = client.get(urllib.parse.urljoin(url, uris[-1]), limit=20_000_000)
    if not segment:
        return {'status': 'failed', 'reason': 'segment_unavailable', 'segment_http': seg_info.get('http')}
    return {'status': 'media_reachable', 'manifest_http': info['http'],
            'segment_http': seg_info['http'], 'segment_bytes': len(segment),
            'encrypted': bool(keys), 'decoded_video': False, 'identity_verified': False}


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def build(text, references, catalog_ids, guide_ids, client, sample=0, prechecked=None):
    lines, entries = parse(text)
    replacement = {}
    for url in references:
        sid = source_id(url)
        if sid in replacement and replacement[sid] != url:
            raise ValueError('conflicting_reference_urls')
        replacement[sid] = url
    lineup_ids = {e.source_id for e in entries}
    if set(replacement) - lineup_ids:
        raise ValueError('reference_not_in_custom_lineup')
    sample_ids = set()
    groups = collections.defaultdict(list)
    for e in entries:
        if e.source_id not in replacement:
            groups[e.group].append(e.source_id)
    preferred = ['USA Sports', 'USA News', 'USA Entertainment', 'USA Locals']
    ordered_groups = [groups[g] for g in preferred if g in groups]
    ordered_groups += [v for g, v in groups.items() if g not in preferred]
    # Round-robin groups; bounded and deterministic, never fuzzy matching.
    for rank in range(sample):
        candidates = [v[rank] for v in ordered_groups if len(v) > rank]
        sample_ids.update(candidates[:max(0, sample - len(sample_ids))])
        if len(sample_ids) >= sample:
            break
    rows = []
    for e in entries:
        row = {'index': e.index, 'name': e.name, 'group': e.group, 'source_id': e.source_id,
               'epg_id': e.epg_id, 'catalog_match': e.source_id in catalog_ids,
               'epg_match': e.epg_id in guide_ids, 'refreshed': False,
               'status': 'retained_unvalidated', 'decoded_video': False,
               'reason': 'no_authorized_current_url_mapping'}
        candidate = replacement.get(e.source_id)
        if candidate or e.source_id in sample_ids:
            result = (prechecked or {}).get(e.source_id)
            if result is None:
                result = test_hls(candidate or e.url, client)
            row.update(result)
            if candidate and result['status'] in ('media_reachable', 'manifest_reachable'):
                ending = '\r\n' if lines[e.line].endswith('\r\n') else '\n' if lines[e.line].endswith('\n') else ''
                lines[e.line] = candidate + ending
                row.update(refreshed=True, reason='exact_user_authorized_url_validated')
            elif candidate:
                row['reason'] = 'current_reference_failed_original_preserved'
        elif prechecked and e.source_id in prechecked:
            row.update(prechecked[e.source_id])
        rows.append(row)
    rebuilt = ''.join(lines)
    _, after = parse(rebuilt)
    if [(e.name, e.group, e.epg_id, e.source_id) for e in after] != [(e.name, e.group, e.epg_id, e.source_id) for e in entries]:
        raise ValueError('lineup_changed')
    changed_lines = {e.line for e in entries if replacement.get(e.source_id) and rows[e.index]['refreshed']}
    original_lines = text.splitlines(keepends=True)
    if any(a != b for i, (a, b) in enumerate(zip(original_lines, lines)) if i not in changed_lines):
        raise ValueError('metadata_changed')
    counts = {'total': len(rows), 'catalog_matched': sum(r['catalog_match'] for r in rows),
              'epg_matched': sum(r['epg_match'] for r in rows), 'refreshed': sum(r['refreshed'] for r in rows),
              'endpoint_tested': sum(r['status'] != 'retained_unvalidated' for r in rows),
              'media_reachable': sum(r['status'] == 'media_reachable' for r in rows),
              'valid_manifest': sum(r['status'] in ('media_reachable', 'manifest_reachable') for r in rows),
              'decoded_video': 0, 'unresolved': sum(not r['refreshed'] for r in rows)}
    report = {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'counts': counts,
              'ready_for_production': False, 'automatic_renewal': 'not_established',
              'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
              'note': 'Only exact authorized URLs are substituted after manifest/key/segment validation. Reachable media is not confirmed playback.',
              'channels': rows}
    return rebuilt, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--references', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--guide', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--expected-count', type=int, default=1916)
    p.add_argument('--sample', type=int, default=8, choices=range(0, 25))
    args = p.parse_args()
    # newline='' preserves all source bytes including CRLF metadata and directives.
    with args.source.open(encoding='utf-8-sig', newline='') as f:
        source = f.read()
    _, entries = parse(source)
    if len(entries) != args.expected_count:
        p.exit(2, 'Source lineup count changed; refusing to discard channels.\n')
    catalog = public_object(args.catalog, 'RALLY_SOURCES_PUBLIC')
    ids = {r['id'] for group in catalog.values() for r in group if r.get('id')}
    with gzip.open(args.guide, 'rb') as f:
        guide = ET.parse(f)
    guide_ids = {e.get('id') for e in guide.getroot().findall('channel')}
    rebuilt, report = build(source, json.loads(args.references.read_text()), ids, guide_ids, Client(), args.sample)
    atomic_write(args.output_dir / 'Binged_All_Streams_REFRESHED.m3u', rebuilt.encode())
    atomic_write(args.output_dir / 'validation-report.json', (json.dumps(report, indent=2) + '\n').encode())
    unresolved = [{k: r[k] for k in ('index', 'name', 'group', 'source_id', 'status', 'reason')} for r in report['channels'] if not r['refreshed']]
    atomic_write(args.output_dir / 'unresolved-channels.json', (json.dumps(unresolved, indent=2) + '\n').encode())
    print(json.dumps(report['counts']))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        # Never print exception messages that may contain credential-bearing URLs.
        raise SystemExit('Refresh stopped safely: ' + type(e).__name__)
