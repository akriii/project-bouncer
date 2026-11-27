import unittest
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from services.scheduler import parse_start_time, compute_drop_stage


class TestScheduler(unittest.IsolatedAsyncioTestCase):

    def test_auto_start_scheduler_parsing(self):
        # 1. Unix timestamp string
        self.assertEqual(parse_start_time("1700000000"), 1700000000.0)

        # 2. ISO 8601 string
        parsed_iso = parse_start_time("2026-11-13T10:00:00Z")
        self.assertGreater(parsed_iso, 1700000000)

        # 3. Drop stage computation
        cfg = {"start_time": "2026-11-13T10:00:00Z", "pre_auth_window_hours": "48"}
        # Far before pre-auth window:
        stage_before = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso - (50 * 3600))
        self.assertEqual(stage_before["stage"], "Scheduled (Pre-Auth Closed)")
        # Inside pre-auth window:
        stage_inside = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso - (10 * 3600))
        self.assertEqual(stage_inside["stage"], "Pre-Auth Open")
        # Ready:
        stage_ready = compute_drop_stage(cfg, is_live=False, now_ts=parsed_iso + 10)
        self.assertEqual(stage_ready["stage"], "Ready (Pending Go-Live)")
        # Live:
        stage_live = compute_drop_stage(cfg, is_live=True)
        self.assertEqual(stage_live["stage"], "Live")


if __name__ == "__main__":
    unittest.main()
