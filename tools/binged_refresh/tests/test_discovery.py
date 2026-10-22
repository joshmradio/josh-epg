from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from authorize import validate_session
from discover import discover
from refresh import parse


class FakeClient:
    def __init__(self, deny=None):
        self.deny = deny
        self.calls = []

    def get(self, url, limit=0):
        self.calls.append(url)
        if self.deny:
            return None, {'http': self.deny}
        return b'#EXTM3U\n#EXTINF:4,\nmedia\n', {'http': 200}


class DiscoveryTests(unittest.TestCase):
    def entries(self):
        text = '#EXTM3U\n'
        for sid in ('one', 'two', 'missing'):
            text += '#EXTINF:-1 tvg-id="' + sid + '",' + sid + '\nhttps://sofi.dulo.cc/play/old/' + sid + '.m3u8\n'
        return parse(text)[1]

    def test_only_exact_catalog_ids_are_requested(self):
        client = FakeClient()
        saved = []
        report = discover(self.entries(), {'one', 'two'}, 'https://sofi.dulo.cc/play/new/one.m3u8', client,
                          lambda report, mappings: saved.append((report, mappings.copy())), interval=0)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(report['valid_manifests'], 2)
        self.assertNotIn('missing', saved[-1][1])
        self.assertEqual(report['channels'][-1]['reason'], 'missing_catalog_id')

    def test_authorization_and_rate_denials_stop_entire_batch(self):
        for status in (401, 403, 429):
            client = FakeClient(status)
            report = discover(self.entries(), {'one', 'two'}, 'https://sofi.dulo.cc/play/new/one.m3u8', client,
                              lambda *_: None, interval=0)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(report['stopped_reason'], 'provider_denial_' + str(status))

    def test_session_requires_valid_server_expiry(self):
        self.assertEqual(validate_session({'sid': 'a' * 32, 'exp': 120001}, 0)['exp'], 120001)
        for bad in ({'sid': 'short', 'exp': 120001}, {'sid': 'a' * 32, 'exp': 1}, {'sid': 'a' * 32, 'exp': True}):
            with self.assertRaises(ValueError):
                validate_session(bad, 0)


if __name__ == '__main__':
    unittest.main()
