"""Export Binged's public schedule as XMLTV. Never reads a PIN or stream session."""
import argparse
import collections
import concurrent.futures
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

UTC = dt.timezone.utc
BASE = 'https://binged.net/data/'


def js_object(text, variable):
    marker = 'window.' + variable
    start = text.index('=', text.index(marker)) + 1
    return json.JSONDecoder().raw_decode(text[start:].lstrip())[0]


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Schedule timestamp has no timezone')
    return parsed.astimezone(UTC)


def fetch(name, variable):
    req = urllib.request.Request(BASE + name, headers={'User-Agent': 'Josh-Binged-XMLTV/1.0'})
    with urllib.request.urlopen(req, timeout=60) as response:
        text = response.read(30_000_001)
    if len(text) > 30_000_000:
        raise ValueError('Source response exceeds limit')
    return js_object(text.decode('utf-8'), variable)


def build(snapshot, catalog, now):
    entries = {}
    for rows in catalog.values():
        for row in rows:
            if row.get('id'):
                entries.setdefault(row['id'], row)
    if not entries:
        raise ValueError('Empty source catalog')
    schedules = collections.defaultdict(list)

    def add(sid, programme):
        if sid in entries and all(programme.get(k) for k in ('n', 's', 'e')):
            schedules[sid].append(programme)

    epg_channels = snapshot.get('epgpw', {}).get('channels', {})
    for name, programmes in epg_channels.items():
        for source in catalog.get(name, []):
            for programme in programmes:
                add(source['id'], programme)
    for name, programmes in snapshot.get('tsn', {}).get('channels', {}).items():
        for source in catalog.get(name, []):
            if not schedules[source['id']]:
                for programme in programmes:
                    add(source['id'], programme)
    aliases = {'SECN': 'SEC Network', 'ACCN': 'ACC Network'}
    for programme in snapshot.get('watch', {}).get('airings', []):
        name = aliases.get(programme.get('net'), programme.get('net'))
        if name in epg_channels:
            continue
        for source in catalog.get(name, []):
            if source.get('via') not in ('event', 'team', 'ppv'):
                add(source['id'], programme)
    for event in snapshot.get('events', []):
        for source in catalog.get(event['id'], []):
            # A network carrying one game does not mean its full schedule is that game.
            if source.get('via') not in ('event', 'team', 'ppv'):
                continue
            add(source['id'], {'n': event.get('title'), 's': event.get('start'),
                'e': event.get('end'), 'sport': event.get('sport'), 'desc': event.get('league')})

    root = ET.Element('tv', {'generator-info-name': 'Josh Binged XMLTV',
        'source-info-name': 'Binged public schedule', 'source-info-url': 'https://binged.net/'})
    total = 0
    current_channels = set()
    latest = now
    for sid, source in sorted(entries.items()):
        channel = ET.SubElement(root, 'channel', {'id': sid})
        ET.SubElement(channel, 'display-name').text = source.get('label', sid)
        unique = {}
        for programme in schedules[sid]:
            try:
                start, stop = timestamp(programme['s']), timestamp(programme['e'])
            except (ValueError, TypeError, KeyError):
                continue
            if stop <= start or stop < now - dt.timedelta(days=1) or start > now + dt.timedelta(days=8):
                continue
            unique.setdefault((start, stop, programme['n']), programme)
        for (start, stop, title), programme in sorted(unique.items()):
            node = ET.SubElement(root, 'programme', {'channel': sid,
                'start': start.strftime('%Y%m%d%H%M%S +0000'),
                'stop': stop.strftime('%Y%m%d%H%M%S +0000')})
            ET.SubElement(node, 'title').text = title
            if programme.get('desc'):
                ET.SubElement(node, 'desc').text = programme['desc']
            if programme.get('sport'):
                ET.SubElement(node, 'category').text = programme['sport']
            total += 1
            if stop > now:
                current_channels.add(sid)
                latest = max(latest, stop)
    status = {'generated_at': now.isoformat(), 'source_updated_at': snapshot.get('fetchedAt'),
        'channels': len(entries), 'channels_with_current_or_future_listings': len(current_channels),
        'programmes': total, 'latest_programme_end': latest.isoformat(),
        'note': 'Only available source listings are included. Blank channels have no reliable listings.'}
    return ET.tostring(root, encoding='utf-8', xml_declaration=True), status


def validate(xml, status, now):
    ET.fromstring(xml)
    if status['channels'] < 100 or status['channels_with_current_or_future_listings'] < 100:
        raise ValueError('Guide coverage is unexpectedly low; keeping published guide')
    if timestamp(status['source_updated_at']) < now - dt.timedelta(hours=6):
        raise ValueError('Upstream schedule is stale; keeping published guide')


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = handle.name
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            os.unlink(temporary)
            raise
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-dir', type=Path)
    parser.add_argument('--output-dir', type=Path, default=Path('.'))
    args = parser.parse_args()
    if args.fixture_dir:
        snapshot = js_object((args.fixture_dir / 'current_snapshot.js').read_text(), 'ESPN_SNAPSHOT')
        catalog = js_object((args.fixture_dir / 'binged_sources_current.js').read_text(), 'RALLY_SOURCES_PUBLIC')
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(fetch, 'snapshot.js', 'ESPN_SNAPSHOT')
            b = pool.submit(fetch, 'sources.js', 'RALLY_SOURCES_PUBLIC')
            snapshot, catalog = a.result(), b.result()
    now = dt.datetime.now(UTC)
    xml, status = build(snapshot, catalog, now)
    validate(xml, status, now)
    atomic_write(args.output_dir / 'binged-guide.xml.gz', gzip.compress(xml, mtime=0))
    atomic_write(args.output_dir / 'binged-guide-status.json', (json.dumps(status, indent=2) + '\n').encode())
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
