"""Explicitly approved, paced catalog-ID validation using a supplied session.

Run only with authorization to reuse the supplied session across this lineup.
One manifest request per second; stop on 401/403/429. No credential discovery,
session minting, hidden endpoints, media proxy, or publishing.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import time
import urllib.parse

from refresh import Client, atomic_write, parse, public_object, source_id


def discover(entries, catalog_ids, reference, client, checkpoint, interval=1.0):
    parts = urllib.parse.urlsplit(reference)
    source_id(reference)  # Validate allowlisted origin/path before reuse.
    prefix = parts.path.rsplit('/', 1)[0]
    rank = {'USA Sports': 0, 'USA News': 1, 'USA Entertainment': 2, 'USA Locals': 3}
    ordered = sorted(entries, key=lambda e: (rank.get(e.group, 4), e.index))
    results, mappings = {}, {}
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    stopped = None
    next_request = 0.0

    def save():
        report = {'started_at': started, 'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'checked': len(results), 'valid_manifests': len(mappings),
                  'stopped_reason': stopped, 'automatic_session_renewal': False,
                  'channels': list(results.values())}
        checkpoint(report, mappings)
        return report

    for e in ordered:
        if e.source_id not in catalog_ids:
            results[e.source_id] = {'source_id': e.source_id, 'status': 'unresolved', 'reason': 'missing_catalog_id'}
            continue
        wait = next_request - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        next_request = time.monotonic() + interval
        candidate = urllib.parse.urlunsplit((parts.scheme, parts.netloc,
                    prefix + '/' + e.source_id + '.m3u8', '', ''))
        data, info = client.get(candidate, limit=262144)
        if info.get('http') in (401, 403, 429):
            stopped = 'provider_denial_' + str(info['http'])
            results[e.source_id] = {'source_id': e.source_id, 'status': 'failed', **info}
            return save()
        text = data.decode('utf-8-sig', errors='replace') if data else ''
        valid = text.lstrip().startswith('#EXTM3U') and ('#EXTINF:' in text or '#EXT-X-STREAM-INF:' in text)
        result = {'source_id': e.source_id, 'status': 'manifest_reachable' if valid else 'failed',
                  'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'manifest_http': info.get('http'), 'decoded_video': False, 'identity_verified': False}
        if not valid:
            result['reason'] = info.get('reason', 'invalid_hls_manifest')
        results[e.source_id] = result
        if valid:
            mappings[e.source_id] = candidate
        if len(results) % 25 == 0:
            save()
            print(json.dumps({'checked': len(results), 'valid_manifests': len(mappings)}), flush=True)
    return save()


class PacedClient(Client):
    def get(self, url, limit=262144):
        # Unlike interactive validation, never retry a rate-limit denial.
        import urllib.error
        import urllib.request
        from refresh import validate_resource
        validate_resource(url)
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'JOSH-IPTV-ALT-Validation/1.0'})
                with self.opener.open(req, timeout=8) as r:
                    data = r.read(limit + 1)
                    if len(data) > limit:
                        return None, {'reason': 'manifest_exceeds_limit'}
                    return data, {'http': r.status}
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 429):
                    return None, {'http': e.code, 'reason': 'provider_denial'}
                if e.code in (502, 503, 504) and attempt == 0:
                    time.sleep(2)
                    continue
                return None, {'http': e.code, 'reason': 'http_error'}
            except (OSError, TimeoutError, ValueError):
                return None, {'reason': 'network_error'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--reference-file', type=Path, required=True)
    p.add_argument('--catalog', type=Path, required=True)
    p.add_argument('--private-output', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--approved-full-lineup', action='store_true', required=True)
    args = p.parse_args()
    _, entries = parse(args.source.read_text())
    if len(entries) != 1916:
        p.exit(2, 'Preserved lineup must contain 1916 entries.\n')
    catalog = public_object(args.catalog, 'RALLY_SOURCES_PUBLIC')
    ids = {r['id'] for rows in catalog.values() for r in rows if r.get('id')}
    refs = json.loads(args.reference_file.read_text())

    def checkpoint(report, mapping):
        atomic_write(args.report, (json.dumps(report, indent=2) + '\n').encode())
        atomic_write(args.private_output, json.dumps(list(mapping.values())).encode())

    try:
        report = discover(entries, ids, refs[0], PacedClient(), checkpoint)
        print(json.dumps({k: report[k] for k in ('checked', 'valid_manifests', 'stopped_reason')}))
    except Exception as e:
        raise SystemExit('Discovery stopped safely: ' + type(e).__name__)
