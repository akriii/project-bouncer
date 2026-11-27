import unittest
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from core.tiers import resolve_tiers


class TestTiers(unittest.IsolatedAsyncioTestCase):

    def test_resolve_tiers_is_a_mix_not_a_ladder(self):
        self.assertEqual(resolve_tiers("org", {"tier2_waf": "true"}), ["tier2_waf"])
        self.assertEqual(resolve_tiers("org", {"tier3_lottery": "true"}), ["tier3_lottery"])
        self.assertEqual(
            resolve_tiers("org", {"tier1_queue": "true", "tier3_lottery": "true"}),
            ["tier1_queue", "tier3_lottery"],
        )
        self.assertEqual(
            resolve_tiers("org", {"tier1_queue": "true", "tier2_waf": "true", "tier3_lottery": "true"}),
            ["tier1_queue", "tier2_waf", "tier3_lottery"],
        )
        self.assertEqual(resolve_tiers("default_client", {}), ["tier1_queue"])
        self.assertEqual(resolve_tiers("default_client", {"tier2_waf": "true"}), ["tier2_waf"])
        self.assertEqual(resolve_tiers("org", {"secret_key": "x"}), [])


if __name__ == "__main__":
    unittest.main()
