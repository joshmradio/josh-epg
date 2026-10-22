"""Paced, two-worker validation using each server's own authorized session."""
import argparse
import concurrent.futures as futures
import datetime as dt
import json
from pathlib import Path
import threading
import time

from discover import PacedClient
from refresh import atomic_write, parse, public_object
from routing import Router, load_config


def run(entries, catalog, config, sessions, checkpoint):
    router = Router(catalog, config)
    ids = {r['id'] for rows in catalog.values() for r in rows if r.get('id')}
    now_ms = time.time() * 1000
    if any(not sessions.get(o, {}).get('sid') or sessions[o].get('exp', 0) < now_ms + 60000 for o in config['origins']):
        raise ValueError('normal_sign_in_required_for_each_configured_origin')
    priority = {'USA Sports': 0, 'USA News': 1, 'USA Entertainment': 2, 'USA Locals': 3}
    queue = iter(sorted((e for e in entries if e.source_id in ids), key=lambda e: (priority.get(e.group, 4), e.index)))
    records = {e.source_id: {'source_id': e.source_id, 'status': 'unresolved', 'reason': 'missing_catalog_id'} for e in entries if e.source_id not in ids}
    mappings = {}
    stop = threading.Event()
    limiter = threading.Lock()
    next_request = [0.0]
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    stopped = None

    def request(origin, sid):
        if stop.is_set():
            return None, {'reason': 'batch_stopped'}, None
        if sessions[origin]['exp'] < time.time() * 1000 + 60000:
            stop.set()
            return None, {'reason': 'session_renewal_required'}, None
        with limiter:
            delay = next_request[0] - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_request[0] = time.monotonic() + 1.0
        if stop.is_set():
            return None, {'reason': 'batch_stopped'}, None
        url = origin + '/play/' + sessions[origin]['sid'] + '/' + sid + '.m3u8'
        data, info = PacedClient().get(url, limit=262144)
        if info.get('http') in (401, 403, 429):
            stop.set()
        return data, info, url

    def check(e):
        home = router.origin(e.source_id)
        selected = [home] + [o for o in config['origins'] if o != home]
        for attempt, origin in enumerate(selected):
            data, info, url = request(origin, e.source_id)
            text = data.decode('utf-8-sig', errors='replace') if data else ''
            valid = text.lstrip().startswith('#EXTM3U') and ('#EXTINF:' in text or '#EXT-X-STREAM-INF:' in text)
            row = {'source_id': e.source_id, 'status': 'manifest_reachable' if valid else 'failed',
                   'origin': origin, 'home_origin': home, 'used_failover': attempt > 0,
                   'manifest_http': info.get('http'), 'decoded_video': False, 'identity_verified': False,
                   'checked_at': dt.datetime.now(dt.timezone.utc).isoformat()}
            if valid:
                return row, url
            row['reason'] = info.get('reason', 'invalid_hls_manifest')
            if stop.is_set() or info.get('http') == 404:
                return row, None
        return row, None

    def save():
        report = {'started_at': started, 'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'checked': sum(r['status'] != 'unresolved' for r in records.values()),
                  'valid_manifests': len(mappings), 'stopped_reason': stopped,
                  'automatic_session_renewal': False, 'routing': 'public_client_per_server',
                  'channels': list(records.values())}
        checkpoint(report, mappings)
        return report

    with futures.ThreadPoolExecutor(max_workers=2) as pool:
        pending = {}

        def submit():
            e = next(queue, None)
            if e is not None:
                pending[pool.submit(check, e)] = e

        submit()
        submit()
        while pending:
            done, _ = futures.wait(pending, return_when=futures.FIRST_COMPLETED)
            for future in done:
                e = pending.pop(future)
                row, url = future.result()
                records[e.source_id] = row
                if url:
                    mappings[e.source_id] = url
                if row.get('manifest_http') in (401, 403, 429):
                    stopped = 'provider_denial_' + str(row['manifest_http'])
                elif row.get('reason') == 'session_renewal_required':
                    stopped = 'session_renewal_required'
                save()  # Every completed check is recoverable without exposing URLs.
                checked = sum(r['status'] != 'unresolved' for r in records.values())
                if checked % 10 == 0:
                    print(json.dumps({'checked': checked, 'valid_manifests': len(mappings)}), flush=True)
                if not stop.is_set():
                    submit()
    return save()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--catalog', required=True, type=Path)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--sessions', required=True, type=Path)
    p.add_argument('--private-output', required=True, type=Path)
    p.add_argument('--report', required=True, type=Path)
    p.add_argument('--approved-full-lineup', action='store_true', required=True)
    a = p.parse_args()
    _, entries = parse(a.source.read_text())
    if len(entries) != 1916:
        p.exit(2, 'Preserved lineup must contain 1916 entries.\n')

    def checkpoint(report, mapping):
        atomic_write(a.report, (json.dumps(report, indent=2) + '\n').encode())
        atomic_write(a.private_output, json.dumps(list(mapping.values())).encode())

    try:
        report = run(entries, public_object(a.catalog, 'RALLY_SOURCES_PUBLIC'), load_config(a.config.read_text()),
                     json.loads(a.sessions.read_text()), checkpoint)
        print(json.dumps({k: report[k] for k in ('checked', 'valid_manifests', 'stopped_reason')}))
    except Exception as e:
        raise SystemExit('Routed validation stopped safely: ' + type(e).__name__)
