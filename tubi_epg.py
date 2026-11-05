"""Builds tubi.xml.gz: an XMLTV guide for Tubi's free live channels, straight from Tubi.

Channel IDs are "tubi.<Tubi content id>", matching the Tubi group in the Josh IPTV playlist.
Runs in GitHub Actions every 4 hours (see .github/workflows/update-tubi-epg.yml). Standard library only.
If Tubi can't be reached or the result looks wrong, the last good tubi.xml.gz is left in place.
"""
import gzip, json, sys, time, uuid, urllib.request
from datetime import datetime, timezone
from xml.sax.saxutils import escape, quoteattr

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
LIST = "https://tubitv.com/oz/epg"
PROG = "https://epg-cdn.production-public.tubi.io/content/epg/programming?platform=web&device_id={dev}&lookahead=1&content_id={ids}"
OUT, STATUS = "tubi.xml.gz", "tubi-guide-status.json"

def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == tries - 1: raise
            print(f"retrying {url[:80]}… ({e})"); time.sleep(5 * (i + 1))

def xt(iso):  # 2026-11-05T17:00:00Z -> 20261105170000 +0000
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y%m%d%H%M%S") + " +0000"

def first(images, *keys):
    for k in keys:
        v = (images or {}).get(k) or []
        if v: return v[0]
    return ""

def main():
    listing = get(LIST)
    contents = listing.get("contents") or {}
    ids = list(contents)
    if len(ids) < 50: sys.exit(f"Only {len(ids)} channels came back from Tubi; keeping the last guide.")
    dev, rows = str(uuid.uuid4()), []
    for i in range(0, len(ids), 40):
        rows += get(PROG.format(dev=dev, ids=",".join(ids[i:i + 40]))).get("rows") or []
    out, progs, ends = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="josh-epg tubi_epg.py">'], 0, []
    for r in rows:
        cid = f"tubi.{r['content_id']}"
        out.append(f'<channel id={quoteattr(cid)}><display-name>{escape(r.get("title") or "")}</display-name>'
                   + (f'<icon src={quoteattr(first(r.get("images"), "thumbnail", "landscape"))}/>' if first(r.get("images"), "thumbnail", "landscape") else "") + "</channel>")
    for r in rows:
        cid = f"tubi.{r['content_id']}"
        for p in r.get("programs") or []:
            if not (p.get("start_time") and p.get("end_time") and p.get("title")): continue
            bits = [f'<programme start="{xt(p["start_time"])}" stop="{xt(p["end_time"])}" channel={quoteattr(cid)}>',
                    f'<title lang="en">{escape(p["title"])}</title>']
            if p.get("episode_title"): bits.append(f'<sub-title lang="en">{escape(p["episode_title"])}</sub-title>')
            if p.get("description"): bits.append(f'<desc lang="en">{escape(p["description"])}</desc>')
            if p.get("year") and str(p["year"]) not in ("0", ""): bits.append(f'<date>{escape(str(p["year"]))}</date>')
            img = first(p.get("images"), "thumbnail", "landscape", "poster")
            if img: bits.append(f"<icon src={quoteattr(img)}/>")
            s, e = p.get("season_number"), p.get("episode_number")
            if s or e: bits.append(f'<episode-num system="onscreen">{"S" + str(s) if s else ""}{"E" + str(e) if e else ""}</episode-num>')
            for rt in p.get("ratings") or []:
                if rt.get("value"): bits.append(f'<rating><value>{escape(rt["value"])}</value></rating>'); break
            bits.append("</programme>"); out.append("".join(bits)); progs += 1; ends.append(p["end_time"])
    out.append("</tv>")
    if progs < len(rows) * 2: sys.exit(f"Only {progs} shows for {len(rows)} channels; keeping the last guide.")
    with gzip.open(OUT, "wb", compresslevel=9) as f: f.write("\n".join(out).encode("utf-8"))
    ends.sort()
    status = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "channels": len(rows), "shows": progs,
              "listings_end_for_most_channels": ends[len(ends) // 2] if ends else None}
    json.dump(status, open(STATUS, "w"), indent=2)
    print(status)

if __name__ == "__main__":
    main()
