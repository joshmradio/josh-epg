import datetime as dt
import unittest
import xml.etree.ElementTree as ET
from binged_epg import build, js_object, timestamp, validate


class GuideTests(unittest.TestCase):
    def test_parses_json_without_executing_javascript(self):
        self.assertEqual(js_object('window.X = {"a": 1}; malicious();', 'X'), {'a': 1})

    def test_event_is_not_applied_to_shared_network(self):
        now = timestamp('2026-10-19T14:00Z')
        snap = {'events': [{'id': 'ev:1', 'title': 'Game', 'start': '2026-10-19T15:00Z',
            'end': '2026-10-19T18:00Z'}]}
        cat = {'ev:1': [{'id': 'network', 'via': 'network'}, {'id': 'team', 'via': 'team'}]}
        xml, status = build(snap, cat, now)
        root = ET.fromstring(xml)
        self.assertEqual([p.get('channel') for p in root.findall('programme')], ['team'])
        self.assertEqual(status['channels'], 2)

    def test_deduplicates_and_rejects_invalid_time_ranges(self):
        now = timestamp('2026-10-19T14:00Z')
        good = {'n': 'Show & More', 's': '2026-10-19T10:00-04:00', 'e': '2026-10-19T11:00-04:00'}
        bad = dict(good, e='2026-10-19T09:00-04:00')
        xml, _ = build({'epgpw': {'channels': {'ABC': [good, good, bad]}}},
            {'ABC': [{'id': 'exact-id', 'label': 'ABC'}]}, now)
        root = ET.fromstring(xml)
        self.assertEqual(len(root.findall('programme')), 1)
        self.assertEqual(root.find('programme').get('start'), '20261019140000 +0000')
        self.assertEqual(root.find('programme').get('channel'), 'exact-id')
        self.assertEqual(root.find('programme/title').text, 'Show & More')

    def test_stale_or_empty_guide_is_not_publishable(self):
        now = timestamp('2026-10-19T14:00Z')
        status = {'channels': 1000, 'channels_with_current_or_future_listings': 500,
            'source_updated_at': '2026-10-18T14:00Z'}
        with self.assertRaisesRegex(ValueError, 'stale'):
            validate(b'<tv/>', status, now)
        status['channels'] = 0
        with self.assertRaisesRegex(ValueError, 'coverage'):
            validate(b'<tv/>', status, now)


if __name__ == '__main__':
    unittest.main()
