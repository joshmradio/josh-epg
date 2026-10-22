import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import Router, load_config, src_hash


class RoutingTests(unittest.TestCase):
    def test_hash_matches_javascript_unsigned_math(self):
        self.assertEqual(src_hash(''), 2872998923)
        self.assertEqual(src_hash('channel_1'), 597542971)
        self.assertEqual(src_hash('Cf74UhxBSq41BWKq'), 3912144900)

    def test_public_config_parses_without_executing_javascript(self):
        text = "window.RALLY_CONFIG={streamOrigins:['https://sofi.dulo.cc','https://2.dulo.cx'],streamWeights:{'https://sofi.dulo.cc':3,'https://2.dulo.cx':2},streamGroupWeights:{'NFL':{'https://2.dulo.cx':55,'https://sofi.dulo.cc':45}}};"
        config = load_config(text)
        self.assertEqual(config['weights']['https://sofi.dulo.cc'], 3)
        self.assertEqual(config['group_weights']['NFL']['https://2.dulo.cx'], 55)

    def test_unknown_origin_requires_review(self):
        with self.assertRaises(ValueError):
            load_config("streamOrigins:['https://unknown.example']")

    def test_group_rank_uses_entire_catalog_not_selected_lineup(self):
        config = {'origins': ['https://sofi.dulo.cc', 'https://2.dulo.cx'], 'weights': {},
                  'group_weights': {'NFL': {'https://2.dulo.cx': 55, 'https://sofi.dulo.cc': 45}}}
        catalog = {'NFL': [{'id': str(i), 'group': 'NFL'} for i in range(100)]}
        router = Router(catalog, config)
        count = sum(router.origin(str(i)) == 'https://2.dulo.cx' for i in range(100))
        self.assertEqual(count, 55)


if __name__ == '__main__':
    unittest.main()
