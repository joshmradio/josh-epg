# Josh EPG

A single combined XMLTV EPG for TiviMax.

It merges:
- EPGShare US2
- EPGShare US_LOCALS1
- EPGShare US_SPORTS1
- EPGShare UK1

The GitHub Action runs twice daily and only replaces the published guide after the new guide downloads, parses, and passes validation. If a build fails, the last known-good `guide.xml.gz` remains in the repository.

## TiviMax EPG URL

https://raw.githubusercontent.com/joshmradio/josh-epg/main/guide.xml.gz

After the first successful GitHub Actions run creates `guide.xml.gz`, use that URL as the playlist's EPG source.

## Tubi guide

`tubi_epg.py` builds `tubi.xml.gz` (Tubi's free live channels, IDs `tubi.<content id>`) straight from Tubi every
4 hours (`.github/workflows/update-tubi-epg.yml`). If Tubi can't be reached, the last good guide stays in place.

https://raw.githubusercontent.com/joshmradio/josh-epg/main/tubi.xml.gz
