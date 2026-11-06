# Josh EPG

A single combined XMLTV EPG for TiviMax.

It merges:
- vcicio US-EPG
- EPGShare US2, US_LOCALS1, US_SPORTS1, UK1, CA2, CH1, PLEX1 (the channels in `target_ids.txt`)
- Pluto TV and Plex free channels (every channel in i.mjh.nz's US Pluto TV and Plex guides, so new FAST
  channels in the playlist get listings without editing anything)

The GitHub Action runs every 2 hours and only replaces the published guide after the new guide downloads, parses, and passes validation. If a build fails, the last known-good `guide.xml.gz` remains in the repository.

## TiviMax EPG URL

https://raw.githubusercontent.com/joshmradio/josh-epg/main/guide.xml.gz

After the first successful GitHub Actions run creates `guide.xml.gz`, use that URL as the playlist's EPG source.

## Tubi guide

`tubi_epg.py` builds `tubi.xml.gz` (Tubi's free live channels, IDs `tubi.<content id>`) straight from Tubi every
4 hours (`.github/workflows/update-tubi-epg.yml`). If Tubi can't be reached, the last good guide stays in place.

https://raw.githubusercontent.com/joshmradio/josh-epg/main/tubi.xml.gz
