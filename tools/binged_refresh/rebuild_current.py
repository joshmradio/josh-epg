"""Construct normal client URLs; keep URL generation separate from availability.

Every new address uses an exact public catalog ID, the site's routing function,
and an unexpired session issued by normal sign-in. No guessed IDs or sessions.
Unvalidated addresses are explicitly unvalidated, not claimed playable. Failed
checks retain the prior address or an individually checked last-known-good URL.
"""
import datetime as dt
import hashlib
import time
from urllib.parse import urlsplit

from refresh import parse
from routing import Router


def rebuild(source, catalog, config, sessions, evidence, validated_urls=None, guide_ids=None):
    lines, entries = parse(source)
    router = Router(catalog, config)
    ids = {r['id'] for rows in catalog.values() for r in rows if r.get('id')}
    rows = []
    validated_urls = validated_urls or {}
    for e in entries:
        row = {'index': e.index, 'name': e.name, 'group': e.group, 'source_id': e.source_id,
               'epg_id': e.epg_id, 'catalog_match': e.source_id in ids,
               'epg_match': e.epg_id in (guide_ids or set()), 'refreshed': False,
               'decoded_video': False, 'identity_verified': False,
               'status': 'unresolved', 'reason': 'missing_catalog_id'}
        url = None
        check = evidence.get(e.source_id)
        if e.source_id in ids:
            origin = router.origin(e.source_id)
            s = sessions.get(origin, {})
            if not s.get('sid') or s.get('exp', 0) < time.time() * 1000 + 60000:
                row.update(status='unresolved', reason='normal_sign_in_required')
            elif check and check['status'] == 'failed':
                row.update(check)
                row.update(reason='availability_check_failed_prior_url_retained')
            else:
                url = origin + '/play/' + s['sid'] + '/' + e.source_id + '.m3u8'
                row.update(status='routing_refreshed_unvalidated', reason='normal_client_url_construction', origin=origin)
                if check:
                    row.update(check)
        fallback = validated_urls.get(e.source_id)
        if fallback:
            parts = urlsplit(fallback)
            fallback_origin = parts.scheme + '://' + parts.netloc
            current = sessions.get(fallback_origin, {})
            expected = '/play/' + current.get('sid', '') + '/' + e.source_id + '.m3u8'
            if (not current.get('sid') or current.get('exp', 0) < time.time() * 1000 + 60000
                    or parts.path != expected or parts.query or parts.fragment):
                fallback = None
        if fallback:
            url = fallback
            if not check or check['status'] != 'failed':
                row['status'] = (check or {}).get('status', 'media_reachable')
                row['reason'] = 'individually_validated_url'
            else:
                row['status'] = 'retained_last_known_good'
                row['reason'] = 'latest_attempt_failed_using_individually_validated_fallback'
        if url:
            ending = '\r\n' if lines[e.line].endswith('\r\n') else '\n' if lines[e.line].endswith('\n') else ''
            lines[e.line] = url + ending
            row['refreshed'] = True
        rows.append(row)
    result = ''.join(lines)
    _, after = parse(result)
    if [(e.name, e.group, e.epg_id, e.source_id) for e in entries] != [(e.name, e.group, e.epg_id, e.source_id) for e in after]:
        raise ValueError('lineup_changed')
    original_lines = source.splitlines(keepends=True)
    url_lines = {e.line for e in entries}
    if any(a != b for i, (a, b) in enumerate(zip(original_lines, lines)) if i not in url_lines):
        raise ValueError('metadata_changed')
    report = {'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'ready_for_production': False, 'automatic_renewal': 'normal_sign_in_tested_hosting_not_configured',
              'counts': {'total': len(rows), 'catalog_matched': sum(r['catalog_match'] for r in rows),
                         'epg_matched': sum(r['epg_match'] for r in rows),
                         'refreshed': sum(r['refreshed'] for r in rows),
                         'manifest_validated': sum(r['status'] in ('manifest_reachable', 'media_reachable') for r in rows),
                         'not_playback_tested': sum(not r.get('decoded_video') for r in rows),
                         'unresolved': sum(not r['refreshed'] for r in rows)},
              'note': 'URL construction follows the normal public client and server-issued sessions. Refreshed URL does not mean confirmed playback. Availability failures preserve prior addresses.',
              'channels': rows}
    return result, report
