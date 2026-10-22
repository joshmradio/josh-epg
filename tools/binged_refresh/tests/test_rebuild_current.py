import sys
from pathlib import Path
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rebuild_current import rebuild
from test_refresh import SOURCE, OLD, NEW, SID


class CurrentRebuildTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'News': [{'id': SID, 'group': 'News'}]}
        self.config = {'origins': ['https://sofi.dulo.cc'], 'weights': {}, 'group_weights': {}}
        self.sessions = {'https://sofi.dulo.cc': {'sid': 'new-session', 'exp': time.time() * 1000 + 3600000}}

    def test_generated_address_is_not_claimed_validated(self):
        result, report = rebuild(SOURCE, self.catalog, self.config, self.sessions, {})
        self.assertEqual(result, SOURCE.replace(OLD, NEW))
        self.assertEqual(report['counts']['manifest_validated'], 0)
        self.assertFalse(report['ready_for_production'])

    def test_expired_fallback_cannot_revive_dead_session(self):
        self.sessions['https://sofi.dulo.cc']['exp'] = 0
        result, report = rebuild(SOURCE, self.catalog, self.config, self.sessions, {}, {SID: NEW})
        self.assertEqual(result, SOURCE)
        self.assertEqual(report['counts']['refreshed'], 0)

    def test_previous_session_evidence_is_not_reused(self):
        result, report = rebuild(SOURCE, self.catalog, self.config, self.sessions,
                                 {SID: {'status': 'failed'}}, {SID: OLD})
        self.assertEqual(result, SOURCE)
        self.assertEqual(report['counts']['refreshed'], 0)

    def test_failed_check_preserves_prior_address(self):
        result, _ = rebuild(SOURCE, self.catalog, self.config, self.sessions, {SID: {'status': 'failed'}})
        self.assertEqual(result, SOURCE)


if __name__ == '__main__':
    unittest.main()
