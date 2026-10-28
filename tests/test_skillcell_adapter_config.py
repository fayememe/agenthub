from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from adapters.skillcell_ah.config import load_skillcell_settings


class SkillcellSettingsTests(unittest.TestCase):
    def test_missing_section_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(yaml.safe_dump({"hub": {"name": "test"}, "nodes": []}))
            settings = load_skillcell_settings(path)
            self.assertFalse(settings.enabled)

    def test_loads_and_normalizes_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "skillcell": {
                            "enabled": True,
                            "state_db": "state.db",
                            "executor_node": "studio",
                            "source_nodes": "studio, edge",
                            "exclude": ["/secret"],
                            "auto_scan": True,
                            "interval_s": 10,
                            "lookback_days": 3,
                            "max_sessions": 999,
                            "message_limit": 0,
                            "min_score": 70,
                            "model": "deepseek/deepseek-flash",
                        }
                    }
                )
            )
            settings = load_skillcell_settings(path)
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.state_db, str(Path(tmp) / "state.db"))
            self.assertEqual(settings.executor_node, "studio")
            self.assertEqual(settings.source_nodes, ("studio", "edge"))
            self.assertEqual(settings.exclude, ("/secret",))
            self.assertEqual(settings.interval_s, 30)
            self.assertEqual(settings.max_sessions, 200)
            self.assertEqual(settings.message_limit, 1)
            self.assertEqual(settings.min_score, 70)
            self.assertEqual(
                settings.model_ref,
                {"providerID": "deepseek", "modelID": "deepseek-flash"},
            )

    def test_invalid_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text(yaml.safe_dump({"skillcell": {"enabled": True, "model": "bad"}}))
            settings = load_skillcell_settings(path)
            with self.assertRaises(ValueError):
                _ = settings.model_ref


if __name__ == "__main__":
    unittest.main()
