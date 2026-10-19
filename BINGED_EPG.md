# Binged EPG

Public XMLTV URL: https://raw.githubusercontent.com/joshmradio/josh-epg/main/binged-guide.xml.gz

The separate Update Binged EPG workflow runs hourly at minute 23 UTC, on manual dispatch, and on changes to its generator/workflow/tests. GitHub scheduling and client caching may delay visible updates. It reads Binged's current public catalog and schedule, validates freshness/coverage, and commits only binged-guide.xml.gz and binged-guide-status.json. Failed builds preserve the previous guide. The existing combined JOSH guide is unchanged.

Use the opaque stream source ID as each M3U tvg-id. No PIN, session, stream URL, or household credential is stored in this guide or workflow. Programme coverage is limited to source listings; unknown channels remain blank. The status JSON reports source and generation timestamps and current/future coverage.

This updates guide listings only. An existing static M3U's session-dependent playback links still expire and need their own renewal mechanism. Reload the playlist separately if new channel entries are needed.

Validate locally with python -m unittest -v test_binged_epg.py and regenerate with python binged_epg.py.

Local stations: fetch the US_LOCALS1 XMLTV feed already used by the JOSH EPG. Match exact station callsigns from local catalog keys to main-station XMLTV IDs. Exact local schedules override generic network listings; missing stations remain blank. The parser streams the large compressed source, and updates fail closed when current local coverage unexpectedly falls below 100 streams.
