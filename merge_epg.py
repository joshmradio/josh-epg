import gzip
import io
import os
import shutil
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET

SOURCES = [
    SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CH1.xml.gz",
],
]

OUTPUT = "guide.xml.gz"
REQUIRED_IDS = {
    "WABC-DT.us_locals1",
    "KYW-DT.us_locals1",
    "ESPN.HD.us2",
    "SkySpMainEvHD.uk",
}

def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Josh-EPG/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    if len(data) < 1000:
        raise RuntimeError(f"Download too small: {url}")
    return data

def parse_gz(data, url):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as g:
            return ET.fromstring(g.read())
    except Exception as e:
        raise RuntimeError(f"Could not parse {url}: {e}") from e

def main():
    roots = []
    for url in SOURCES:
        print(f"Downloading {url}")
        roots.append(parse_gz(download(url), url))

    out = ET.Element("tv", {"generator-info-name": "Josh EPG Merge"})
    channels = {}
    programme_keys = set()
    programmes = []

    for root in roots:
        for ch in root.findall("channel"):
            cid = ch.get("id")
            if cid and cid not in channels:
                channels[cid] = ch

    missing = REQUIRED_IDS - set(channels)
    if missing:
        raise RuntimeError("Required guide IDs missing: " + ", ".join(sorted(missing)))

    for cid in sorted(channels):
        out.append(channels[cid])

    for root in roots:
        for pr in root.findall("programme"):
            key = (
                pr.get("channel"),
                pr.get("start"),
                pr.get("stop"),
                pr.findtext("title"),
            )
            if key not in programme_keys:
                programme_keys.add(key)
                programmes.append(pr)

    if len(channels) < 500:
        raise RuntimeError(f"Validation failed: only {len(channels)} channels")
    if len(programmes) < 10000:
        raise RuntimeError(f"Validation failed: only {len(programmes)} programmes")

    for pr in programmes:
        out.append(pr)

    xml_bytes = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    tmp = OUTPUT + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=9) as f:
        f.write(xml_bytes)

    # Atomic replacement: if anything above fails, the previous guide remains intact.
    os.replace(tmp, OUTPUT)
    print(f"Published {OUTPUT}: {len(channels)} channels, {len(programmes)} programmes")

if __name__ == "__main__":
    main()
