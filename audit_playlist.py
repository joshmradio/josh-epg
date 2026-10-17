#!/usr/bin/env python3
"""Audit M3U channel IDs against the locally generated XMLTV guide.
Usage: python audit_playlist.py playlist.m3u guide.xml.gz
This tool never invents EPG IDs or alters the playlist.
"""
import csv, gzip, re, sys, xml.etree.ElementTree as ET
from collections import Counter

def main(playlist, guide):
    data = open(playlist, encoding="utf-8-sig").read().splitlines()
    opener = gzip.open if guide.endswith(".gz") else open
    with opener(guide, "rb") as f:
        root = ET.parse(f).getroot()
    ids = {e.get("id") for e in root.findall("channel")}
    counts = Counter(p.get("channel") for p in root.findall("programme"))
    rows = []
    for line in data:
        if not line.startswith("#EXTINF"):
            continue
        name = line.rsplit(",", 1)[-1].strip()
        ident = re.search(r'tvg-id="([^"]*)"', line)
        group = re.search(r'group-title="([^"]*)"', line)
        cid = ident.group(1) if ident else ""
        group = group.group(1) if group else ""
        if group == "24/7 CHANNELS":
            status = "EXCLUDED_24_7"
        elif not cid:
            status = "MISSING_ID"
        elif cid not in ids:
            status = "ID_NOT_IN_GUIDE"
        elif counts[cid] == 0:
            status = "NO_PROGRAMMES"
        else:
            status = "HAS_PROGRAMMES"
        rows.append((name, group, cid, status, counts[cid]))
    with open("epg_audit.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(("name", "group", "tvg_id", "status", "programme_count"))
        w.writerows(rows)
    print("Channels:", len(rows))
    for status, n in sorted(Counter(r[3] for r in rows).items()):
        print(status, n)
    print("Wrote epg_audit.csv")

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("Usage: python audit_playlist.py playlist.m3u guide.xml.gz")
    main(*sys.argv[1:])
