"""Builds guide.xml.gz: the listings for the channels in target_ids.txt, from the EPGShare files (and a few others).

What happened on 24 Oct 2026: EPGShare stopped updating on 21 Oct, its listings ran out at 1 AM ET, and the guide
went blank for most channels (locals included) with no warning, because every run still "succeeded". Now:
  * every file is downloaded fresh (past Cloudflare's 4-hour cache) and must actually hold listings; if not, it is
    downloaded again a couple of minutes later, up to 4 times;
  * a channel the new downloads leave without listings keeps its still-useful listings from the previous guide;
  * if the new guide would cover clearly fewer channels than the one already published, it is not published;
  * guide-status.json says how far ahead the listings go and when each source last updated, and the workflow's
    last step fails (so GitHub emails you) when most channels have less than a day of listings left.
"""
import json
import copy
import gzip
import io
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SOURCES = [
    "https://vcicio.github.io/US-EPG/merged_epg.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CH1.xml.gz",
    "https://i.mjh.nz/PlutoTV/us.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz",
]
# Sources the guide can't do without: if one of these still has no listings after every retry, nothing is published.
REQUIRED = {
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
}
OUTPUT = "guide.xml.gz"
# Old channel id in target_ids.txt -> the id a source uses now.
CHANNEL_ID_ALIASES = {
    "SkyPremiereHD.uk": "Sky.Premiere.uk",
    "AMCThrillers.us@SD": "plex.tv.AMC.Thrillers.plex",
    "Sky.ScFi/HorHD.uk": "Sky.Sci-Fi.HD.uk",
}
TRIES = 4
RETRY_WAIT_S = 120
KEEP_ENDED_H = 6          # listings carried over from the previous guide: keep those that ended less than this ago
MIN_COVERAGE_RATIO = 0.85  # the new guide must cover at least this share of what the published one covers now


def log(*a):
    print(*a, flush=True)


def xmltv_time(s):
    """'20261024163000 +0000' -> UTC datetime (None if unreadable)."""
    try:
        s = (s or "").strip()
        t = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        off = s[14:].strip()
        if off:
            sign = -1 if off[0] == "-" else 1
            mins = int(off[1:3]) * 60 + int(off[3:5])
            return (t.replace(tzinfo=timezone.utc).timestamp() - sign * mins * 60)
        return t.replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def download(url):
    sep = "&" if "?" in url else "?"
    fresh = f"{url}{sep}nocache={int(time.time())}"      # past Cloudflare's cache, which can hold a half-written file
    req = urllib.request.Request(fresh, headers={"User-Agent": "Josh-EPG/2.0", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
        modified = r.headers.get("Last-Modified", "")
    if len(data) < 1000:
        raise RuntimeError(f"download too small ({len(data)} bytes)")
    LAST_MODIFIED[url] = modified
    return data


LAST_MODIFIED = {}


def read_source(data, want):
    """Streams one XMLTV file. Returns (channels for ids in `want`, programmes for those ids, total programmes,
    ACC Network candidate ids). Everything else is skipped, so the big locals file needs little memory."""
    channels, progs, total, acc = {}, {}, 0, []
    raw = gzip.GzipFile(fileobj=io.BytesIO(data)) if data[:2] == b"\x1f\x8b" else io.BytesIO(data)
    for _, el in ET.iterparse(raw, events=("end",)):
        if el.tag == "programme":
            total += 1
            cid = el.get("channel")
            if cid in want or cid in acc:
                progs.setdefault(cid, []).append(el)
            else:
                el.clear()
        elif el.tag == "channel":
            cid = el.get("id") or ""
            names = ["".join(c for c in (n.text or "").upper() if c.isalnum()) for n in el.findall("display-name")]
            flat = "".join(c for c in cid.upper() if c.isalnum())
            if "ACCNETWORK" in names or "ACCN" in names or "ACCNETWORK" in flat:
                acc.append(cid)
                channels.setdefault(cid, el)
            elif cid in want:
                channels.setdefault(cid, el)
    return channels, progs, total, acc


def fetch_source(url, want):
    last_err = None
    for attempt in range(1, TRIES + 1):
        try:
            data = download(url)
            chans, progs, total, acc = read_source(data, want)
            wanted_here = [c for c in chans if c in want]
            with_listings = [c for c in wanted_here if progs.get(c)]
            log(f"  try {attempt}: {len(data):,} bytes, {total:,} listings in the file, "
                f"{len(with_listings)}/{len(wanted_here)} of our channels have listings")
            # A file that names our channels but has no listings for them is mid-rewrite (or broken): try again
            if total == 0 or (len(wanted_here) >= 3 and not with_listings):
                raise RuntimeError("the file has channels but (almost) no listings")
            return chans, progs, acc, total
        except Exception as e:
            last_err = e
            log(f"  try {attempt} failed: {e}")
            if attempt < TRIES:
                time.sleep(RETRY_WAIT_S)
    raise RuntimeError(f"{url}: {last_err}")


def load_previous():
    """The guide published now, for carrying listings over and for the coverage check."""
    if not os.path.exists(OUTPUT):
        return {}, {}
    try:
        chans, progs, _, _ = read_source(open(OUTPUT, "rb").read(), _AnyId())
        return chans, progs
    except Exception as e:
        log(f"WARNING couldn't read the previous {OUTPUT}: {e}")
        return {}, {}


class _AnyId:
    def __contains__(self, _):
        return True


def coverage(progs, ids, now):
    """How many of `ids` have something on now or in the next 6 hours."""
    n = 0
    for cid in ids:
        for p in progs.get(cid, ()):
            start, stop = xmltv_time(p.get("start")), xmltv_time(p.get("stop"))
            if start is not None and stop is not None and stop > now and start < now + 6 * 3600:
                n += 1
                break
    return n


def main():
    now = time.time()
    with open("target_ids.txt", "r", encoding="utf-8") as f:
        target_ids = {line.strip() for line in f if line.strip() and not line.startswith("#")}
    want = set(target_ids) | set(CHANNEL_ID_ALIASES.values())

    channels, programmes, acc_candidates, broken, source_info = {}, {}, [], [], {}
    for url in SOURCES:
        log(f"Downloading {url}")
        try:
            chans, progs, acc, total = fetch_source(url, want)
        except Exception as e:
            log(f"WARNING {e}")
            broken.append(url)
            source_info[url] = {"ok": False, "error": str(e)[:300]}
            continue
        source_info[url] = {"ok": True, "last_modified": LAST_MODIFIED.get(url, ""), "listings_in_file": total}
        for cid, el in chans.items():
            channels.setdefault(cid, el)
        for cid, lst in progs.items():
            programmes.setdefault(cid, []).extend(lst)
        acc_candidates += [c for c in acc if c not in acc_candidates]

    missing_required = [u for u in broken if u in REQUIRED]
    if missing_required:
        log(f"ERROR required sources had no listings after {TRIES} tries: {missing_required}")
        log("The published guide is left as it is.")
        sys.exit(1)

    # Old ids that a source renamed
    for legacy_id, current_id in CHANNEL_ID_ALIASES.items():
        if current_id in channels:
            alias = copy.deepcopy(channels[current_id]); alias.set("id", legacy_id)
            channels[legacy_id] = alias
            programmes[legacy_id] = programmes.get(current_id, [])
            log(f"EPG_ALIAS {legacy_id} -> {current_id}")
        else:
            log(f"WARNING alias source missing: {current_id}")

    # ACC Network has different ids in different guides: publish ACC.Network.us from whichever has the most listings
    acc_source = max(acc_candidates, key=lambda c: len(programmes.get(c, [])), default=None)
    if acc_source and programmes.get(acc_source):
        alias = copy.deepcopy(channels[acc_source]); alias.set("id", "ACC.Network.us")
        channels["ACC.Network.us"] = alias
        programmes["ACC.Network.us"] = programmes[acc_source]
        target_ids.add("ACC.Network.us")
        log(f"EPG_ALIAS ACC.Network.us -> {acc_source} ({len(programmes[acc_source])} listings)")
    else:
        log(f"WARNING ACC Network has no listings upstream; candidates={acc_candidates}")

    # Carry over: a channel left without anything current keeps the previous guide's still-useful listings
    prev_chans, prev_progs = load_previous()
    carried = []
    for cid in sorted(target_ids):
        if coverage(programmes, [cid], now):
            continue
        keep = [p for p in prev_progs.get(cid, ()) if (xmltv_time(p.get("stop")) or 0) > now - KEEP_ENDED_H * 3600]
        if keep:
            programmes[cid] = keep
            if cid not in channels and cid in prev_chans:
                channels[cid] = prev_chans[cid]
            carried.append(cid)
    if carried:
        log(f"CARRIED {len(carried)} channels from the previous guide: {', '.join(carried[:20])}{' …' if len(carried) > 20 else ''}")

    found = sorted(cid for cid in target_ids if cid in channels)
    for cid in sorted(target_ids - set(found)):
        log(f"MISSING_ID {cid}")

    # Checks before publishing
    new_cov = coverage(programmes, found, now)
    old_cov = coverage(prev_progs, target_ids, now)
    blank = [cid for cid in found if not coverage(programmes, [cid], now)]
    log(f"Target IDs: {len(target_ids)}; found: {len(found)}; with listings now or soon: {new_cov} "
        f"(published guide: {old_cov})")
    if blank:
        log(f"BLANK now: {', '.join(blank[:40])}{' …' if len(blank) > 40 else ''}")
    total = sum(len(programmes.get(cid, [])) for cid in found)
    if len(found) < 50 or total < 1000:
        raise RuntimeError(f"Filtered guide unexpectedly small: {len(found)} channels, {total} listings")
    if old_cov and new_cov < old_cov * MIN_COVERAGE_RATIO:
        raise RuntimeError(f"New guide covers {new_cov} channels, the published one {old_cov}: not publishing")

    out = ET.Element("tv", {"generator-info-name": "Josh EPG Filtered Merge"})
    for cid in found:
        out.append(channels[cid])
    seen, count = set(), 0
    for cid in found:
        for p in programmes.get(cid, ()):
            key = (p.get("start"), p.get("stop"), p.findtext("title"))
            if (cid, key) in seen:
                continue
            seen.add((cid, key))
            if p.get("channel") != cid:
                p = copy.deepcopy(p); p.set("channel", cid)
            out.append(p); count += 1
    xml_bytes = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    tmp = OUTPUT + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=9) as f:
        f.write(xml_bytes)
    os.replace(tmp, OUTPUT)
    log(f"Published FILTERED {OUTPUT}: {len(found)} channels, {count} listings, {new_cov} with listings now or soon")

    # How far ahead the listings go: the workflow warns you when they are running out
    ends = {}
    for cid in found:
        stops = [xmltv_time(p.get("stop")) or 0 for p in programmes.get(cid, ())]
        if stops:
            ends[cid] = max(stops)
    day_ahead = sum(1 for cid, e in ends.items() if e > now + 24 * 3600)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="minutes")
    status = {
        "generated_at": iso(now),
        "channels": len(found),
        "with_listings_now_or_soon": new_cov,
        "with_a_day_of_listings_left": day_ahead,
        "listings_end_for_most_channels": iso(sorted(ends.values())[len(ends) // 2]) if ends else None,
        "carried_from_previous_guide": carried,
        "blank_now": blank,
        "sources": source_info,
    }
    with open("guide-status.json", "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    log(f"Channels with at least a day of listings left: {day_ahead} of {new_cov}")


if __name__ == "__main__":
    main()
