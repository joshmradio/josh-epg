import gzip
import io
import os
import urllib.request
import xml.etree.ElementTree as ET
import copy

SOURCES = [
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CH1.xml.gz",
    "https://i.mjh.nz/PlutoTV/us.xml.gz",
]

OUTPUT = "guide.xml.gz"
REQUIRED_IDS = {"WABC-DT.us_locals1", "KYW-DT.us_locals1", "ESPN.HD.us2", "SkySpMainEvHD.uk"}
PLUTO_TARGETS = ("hit sitcom", "80s rewind", "90s throwback", "comedy", "vevo", "yo! mtv")
SKY_TARGETS = ("sky cinema", "sky premiere", "sky action", "sky comedy", "sky family", "sky thriller", "sky sci", "sky drama", "sky great", "sky hits", "sky select")

# Preserve the legacy Sky Cinema IDs that TiviMax previously matched successfully.
# Each alias gets a copy of the current UK1 channel and all of its programme records.
SKY_LEGACY_ALIASES = {
    "SkyPremiereHD.uk": "Sky.Premiere.uk",
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

def print_matches(root, label, targets):
    counts = {}
    for p in root.findall("programme"):
        cid = p.get("channel")
        counts[cid] = counts.get(cid, 0) + 1
    print(label)
    for ch in root.findall("channel"):
        names = [n.text or "" for n in ch.findall("display-name")]
        hay = " | ".join(names).lower()
        if any(t in hay for t in targets):
            cid = ch.get("id")
            print(f"{label.split()[0]}_ID {cid} :: {' | '.join(names)} :: PROGRAMMES={counts.get(cid, 0)}")

def main():
    roots = []
    for url in SOURCES:
        print(f"Downloading {url}")
        roots.append(parse_gz(download(url), url))

    print_matches(roots[-1], "PLUTO EPG TARGET MATCHES:", PLUTO_TARGETS)
    print_matches(roots[3], "SKY EPG TARGET MATCHES:", SKY_TARGETS)

    out = ET.Element("tv", {"generator-info-name": "Josh EPG Merge"})
    channels = {}
    programme_keys = set()
    programmes = []
    for root in roots:
        for ch in root.findall("channel"):
            cid = ch.get("id")
            if cid and cid not in channels:
                channels[cid] = ch

    # Add legacy Sky channel IDs as aliases of the current UK1 IDs.
    for legacy_id, current_id in SKY_LEGACY_ALIASES.items():
        if current_id in channels:
            alias_ch = copy.deepcopy(channels[current_id])
            alias_ch.set("id", legacy_id)
            channels[legacy_id] = alias_ch
            print(f"SKY_ALIAS {legacy_id} -> {current_id}")
        else:
            raise RuntimeError(f"Sky alias source missing: {current_id}")

    missing = REQUIRED_IDS - set(channels)
    if missing:
        raise RuntimeError("Required guide IDs missing: " + ", ".join(sorted(missing)))
    for cid in sorted(channels):
        out.append(channels[cid])

    for root in roots:
        for pr in root.findall("programme"):
            key = (pr.get("channel"), pr.get("start"), pr.get("stop"), pr.findtext("title"))
            if key not in programme_keys:
                programme_keys.add(key)
                programmes.append(pr)

            # Duplicate current Sky programme records onto the legacy IDs.
            for legacy_id, current_id in SKY_LEGACY_ALIASES.items():
                if pr.get("channel") == current_id:
                    alias_pr = copy.deepcopy(pr)
                    alias_pr.set("channel", legacy_id)
                    alias_key = (legacy_id, alias_pr.get("start"), alias_pr.get("stop"), alias_pr.findtext("title"))
                    if alias_key not in programme_keys:
                        programme_keys.add(alias_key)
                        programmes.append(alias_pr)

    if len(channels) < 500 or len(programmes) < 10000:
        raise RuntimeError("Validation failed")
    for pr in programmes:
        out.append(pr)
    xml_bytes = ET.tostring(out, encoding="utf-8", xml_declaration=True)
    tmp = OUTPUT + ".tmp"
    with gzip.open(tmp, "wb", compresslevel=9) as f:
        f.write(xml_bytes)
    os.replace(tmp, OUTPUT)
    print(f"Published {OUTPUT}: {len(channels)} channels, {len(programmes)} programmes")

if __name__ == "__main__":
    main()
