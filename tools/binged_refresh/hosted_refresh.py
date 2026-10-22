"""Draft hosted runner: normal auth, validation, optional scoped Dropbox upload.

Deployment and secret storage require user approval. Never prints secrets, URLs,
or response bodies; generated playlists must not be committed or made artifacts.
"""
import argparse
import base64
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from authorize import authenticate
from discover_routed import run
from refresh import atomic_write, build, parse, public_object
from routing import load_config


def download(url, limit=50_000_000):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ValueError('https_required')
    with urllib.request.urlopen(url, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('download_exceeds_limit')
    return data


def publish_dropbox(data):
    body = urllib.parse.urlencode({'grant_type': 'refresh_token',
        'client_id': os.environ['DROPBOX_APP_KEY'],
        'refresh_token': os.environ['DROPBOX_REFRESH_TOKEN']}).encode()
    request = urllib.request.Request('https://api.dropboxapi.com/oauth2/token', data=body)
    with urllib.request.urlopen(request, timeout=30) as response:
        access = json.load(response)['access_token']
    # Intended for an app-folder-scoped Dropbox app, not /BINGED or /IPTV.
    target = '/Binged_All_Streams_REFRESHED.m3u'
    request = urllib.request.Request('https://content.dropboxapi.com/2/files/upload', data=data,
        headers={'Authorization': 'Bearer ' + access, 'Content-Type': 'application/octet-stream',
                 'Dropbox-API-Arg': json.dumps({'path': target, 'mode': 'overwrite', 'autorename': False, 'mute': True})})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get('path_lower') != target.lower():
        raise ValueError('unexpected_publish_target')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--publish', action='store_true')
    p.add_argument('--audit-only', action='store_true', help='Test normal hosted login and five previously approved sample channels')
    args = p.parse_args()
    if args.audit_only and args.publish:
        raise ValueError('audit_cannot_publish')
    report_dir = Path(os.environ.get('REPORT_DIR', 'reports'))
    report_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        packed = os.environ.get('BINGED_LINEUP_GZIP_1', '') + os.environ.get('BINGED_LINEUP_GZIP_2', '')
        if not packed:
            raise ValueError('preserved_lineup_secret_missing')
        source = gzip.decompress(base64.b64decode(packed, validate=True)).decode('utf-8-sig')
        _, entries = parse(source)
        if len(entries) != 1916:
            raise ValueError('source_lineup_changed')
        for name in ('sources.js', 'config.js'):
            (work / name).write_bytes(download('https://binged.net/data/' + name))
        catalog = public_object(work / 'sources.js', 'RALLY_SOURCES_PUBLIC')
        config = load_config((work / 'config.js').read_text())
        pin = os.environ['BINGED_HOUSE_PIN']
        sessions = {origin: authenticate(origin, pin) for origin in config['origins']}
        pin = None
        evidence, mappings = {}, {}

        def checkpoint(report, found):
            evidence.clear()
            evidence.update({r['source_id']: r for r in report['channels']})
            mappings.clear()
            mappings.update(found)
            atomic_write(report_dir / 'discovery-report.json', (json.dumps(report, indent=2) + '\n').encode())

        sample_ids = {'Cf74UhxBSq41BWKq', 'ghBaZ82m1zEXBtOU', '_0TlsvLo8uUX-R5U',
                      'BXvc9FJX9oCnsV-A', 'i9qyXSIRmoC_uE_z'}
        selected = [e for e in entries if e.source_id in sample_ids] if args.audit_only else entries
        if args.audit_only and len(selected) != len(sample_ids):
            raise ValueError('audit_samples_missing')
        discovery = run(selected, catalog, config, sessions, checkpoint)
        if discovery['stopped_reason']:
            raise ValueError('provider_denial_or_renewal_required')
        guide_bytes = download('https://raw.githubusercontent.com/joshmradio/josh-epg/main/binged-guide.xml.gz')
        guide = ET.fromstring(gzip.decompress(guide_bytes))
        guide_ids = {e.get('id') for e in guide.findall('channel')}
        ids = {r['id'] for rows in catalog.values() for r in rows if r.get('id')}
        rebuilt, report = build(source, list(mappings.values()), ids, guide_ids, None, prechecked=evidence)
        report['session_expiry_utc'] = {o: dt.datetime.fromtimestamp(s['exp'] / 1000, dt.timezone.utc).isoformat() for o, s in sessions.items()}
        report['hosted_login_tested'] = True
        report['audit_only'] = args.audit_only
        report['published'] = False
        minimum = int(os.environ.get('MIN_REFRESHED_CHANNELS', '0'))
        if args.publish:
            if minimum < 1 or report['counts']['refreshed'] < minimum:
                raise ValueError('coverage_below_approved_baseline')
            publish_dropbox(rebuilt.encode())
            report['published'] = True
        # Public repository artifacts must not disclose the private selected lineup.
        report.pop('channels', None)
        atomic_write(report_dir / 'validation-report.json', (json.dumps(report, indent=2) + '\n').encode())
        print(json.dumps({'counts': report['counts'], 'published': report['published']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        raise SystemExit('Hosted refresh stopped safely: ' + type(e).__name__)
