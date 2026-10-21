import gzip
import io
import os
import urllib.request
import xml.etree.ElementTree as ET
import copy

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
# AMC Thrillers is included in target_ids.txt; keep its source ID unchanged.
OUTPUT = "guide.xml.gz"
CHANNEL_ID_ALIASES = {
    "SkyPremiereHD.uk": "Sky.Premiere.uk",
    "AMCThrillers.us@SD": "plex.tv.AMC.Thrillers.plex",
    "Sky.ScFi/HorHD.uk": "Sky.Sci-Fi.HD.uk",
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
    with open("target_ids.txt", "r", encoding="utf-8") as f:
        target_ids = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    roots = []
    for url in SOURCES:
        print(f"Downloading {url}")
        roots.append(parse_gz(download(url), url))

    channels = {}
    for root in roots:
        for ch in root.findall("channel"):
            cid = ch.get("id")
            if cid and cid not in channels:
                channels[cid] = ch

    for legacy_id, current_id in CHANNEL_ID_ALIASES.items():
        if current_id not in channels:
            raise RuntimeError(f"EPG alias source missing: {current_id}")
        alias = copy.deepcopy(channels[current_id])
        alias.set("id", legacy_id)
        channels[legacy_id] = alias
        print(f"EPG_ALIAS {legacy_id} -> {current_id}")

    # ACC Network uses different IDs across XMLTV providers. Publish a stable
    # ACC.Network.us alias with real programme data from the available source.
    acc_candidates = []
    for root in roots:
        for ch in root.findall("channel"):
            cid = ch.get("id", "")
            names = [n.text.strip() for n in ch.findall("display-name") if n.text]
            normalized = ["".join(c for c in name.upper() if c.isalnum()) for name in names]
            if ("ACCNETWORK" in normalized or "ACCN" in normalized or
                    "ACCNETWORK" in "".join(c for c in cid.upper() if c.isalnum())):
                if cid not in acc_candidates:
                    acc_candidates.append(cid)
    programme_counts = {}
    for root in roots:
        for pr in root.findall("programme"):
            cid = pr.get("channel")
            if cid in acc_candidates:
                programme_counts[cid] = programme_counts.get(cid, 0) + 1
    acc_source = max(acc_candidates, key=lambda cid: programme_counts.get(cid, 0), default=None)
    if acc_source and programme_counts.get(acc_source, 0):
        alias = copy.deepcopy(channels[acc_source])
        alias.set("id", "ACC.Network.us")
        channels["ACC.Network.us"] = alias
        target_ids.add("ACC.Network.us")
        CHANNEL_ID_ALIASES["ACC.Network.us"] = acc_source
        print(f"EPG_ALIAS ACC.Network.us -> {acc_source} ({programme_counts[acc_source]} programmes)")
    else:
        print(f"WARNING ACC Network has no programmes in upstream sources; candidates={acc_candidates}")

    found = target_ids & set(channels)
    missing = target_ids - set(channels)
    print(f"Target IDs: {len(target_ids)}; found: {len(found)}; missing: {len(missing)}")
    for cid in sorted(missing):
        print(f"MISSING_ID {cid}")

    out = ET.Element("tv", {"generator-info-name": "Josh EPG Filtered Merge"})
    for cid in sorted(found):
        out.append(channels[cid])

    seen = set()
    programmes = []
    for root in roots:
        for pr in root.findall("programme"):
            cid = pr.get("channel")
            if cid in found:
                key = (cid, pr.get("start"), pr.get("stop"), pr.findtext("title"))
                if key not in seen:
                    seen.add(key)
                    programmes.append(pr)
            for legacy_id, current_id in CHANNEL_ID_ALIASES.items():
                if legacy_id in found and cid == current_id:
                    alias = copy.deepcopy(pr)
                    alias.set("channel", legacy_id)
                    key = (legacy_id, alias.get("start"), alias.get("stop"), alias.findtext("title"))
                    if key not in seen:
                        seen.add(key)
                        programmes.append(alias)

    if len(found) < 50 or len(programmes) < 1000:
        raise RuntimeError(f"Filtered guide unexpectedly small: {len(found)} channels, {len(programmes)} programmes")

    for pr in programmes:
        out.append(pr)
    xml_bytes = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    tmp = OUTPUT + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=9) as f:
        f.write(xml_bytes)
    os.replace(tmp, OUTPUT)
    print(f"Published FILTERED {OUTPUT}: {len(found)} channels, {len(programmes)} programmes")

if __name__ == "__main__":
    main()
