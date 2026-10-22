import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from refresh import build, parse, source_id, test_hls

SID = 'channel_1'
OLD = 'https://sofi.dulo.cc/play/old-session/channel_1.m3u8'
NEW = 'https://sofi.dulo.cc/play/new-session/channel_1.m3u8'
SOURCE = ('#EXTM3U url-tvg="https://example.com/guide.xml"\r\n'
          '#EXTINF:-1 tvg-id="epg.one" tvg-logo="https://example.com/a,b.png" group-title="News",Name, with comma\r\n'
          '#EXTVLCOPT:http-user-agent=Example\r\n' + OLD + '\r\n')


class FakeClient:
    def __init__(self, fail=False, segment_fail=False):
        self.fail = fail
        self.segment_fail = segment_fail
        self.requested = []

    def get(self, url, limit=0):
        self.requested.append(url)
        if self.fail:
            return None, {'http': 403}
        if url.endswith('.m3u8'):
            return b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nsegment\n', {'http': 200}
        if self.segment_fail:
            return None, {'http': 503}
        return b'video-bytes', {'http': 200}


class RefreshTests(unittest.TestCase):
    def test_exact_metadata_and_order_preserved(self):
        result, report = build(SOURCE, [NEW], {SID}, {'epg.one'}, FakeClient())
        self.assertEqual(result, SOURCE.replace(OLD, NEW))
        self.assertEqual(report['counts']['refreshed'], 1)
        self.assertEqual(report['counts']['decoded_video'], 0)

    def test_failed_reference_keeps_original(self):
        result, report = build(SOURCE, [NEW], {SID}, set(), FakeClient(fail=True))
        self.assertEqual(result, SOURCE)
        self.assertEqual(report['counts']['refreshed'], 0)
        self.assertEqual(report['channels'][0]['http'], 403)

    def test_no_shared_session_extrapolation(self):
        second = SOURCE + '#EXTINF:-1 tvg-id="two",Second\r\nhttps://2.dulo.cx/play/other/channel_2.m3u8\r\n'
        result, report = build(second, [NEW], {SID, 'channel_2'}, set(), FakeClient())
        self.assertIn('https://2.dulo.cx/play/other/channel_2.m3u8', result)
        self.assertEqual(report['counts']['unresolved'], 1)

    def test_duplicate_ids_fail_instead_of_discarding(self):
        with self.assertRaises(ValueError):
            parse(SOURCE + '#EXTINF:-1,Duplicate\n' + OLD + '\n')

    def test_foreign_urls_rejected(self):
        with self.assertRaises(ValueError):
            source_id('https://evil.example/play/a/channel_1.m3u8')

    def test_unselected_channel_not_restored(self):
        with self.assertRaises(ValueError):
            build(SOURCE, ['https://sofi.dulo.cc/play/session/unselected.m3u8'], set(), set(), FakeClient())

    def test_manifest_without_media_is_not_success(self):
        result = test_hls(NEW, FakeClient(segment_fail=True))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'segment_unavailable')

    def test_reports_never_contain_stream_sessions(self):
        import json
        _, report = build(SOURCE, [NEW], {SID}, set(), FakeClient())
        serialized = json.dumps(report)
        self.assertNotIn('new-session', serialized)
        self.assertNotIn('old-session', serialized)
        self.assertNotIn('/play/', serialized)


if __name__ == '__main__':
    unittest.main()
