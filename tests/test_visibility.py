from __future__ import annotations

import unittest

from app.visibility import DAY_MS, project_visibility


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000_000
        self.project = {
            "kind": "project",
            "last_session": self.now - 120 * DAY_MS,
            "updated": self.now - 120 * DAY_MS,
            "busy_sessions": 0,
            "pending_permissions": 0,
            "pending_questions": 0,
        }

    def check(self, **overrides):
        project = {**self.project, **overrides.pop("project", {})}
        return project_visibility(
            project,
            manual_hidden=overrides.pop("manual_hidden", False),
            favorite=overrides.pop("favorite", False),
            hide_stale_days=overrides.pop("hide_stale_days", 90),
            now_ms=self.now,
        )

    def test_stale_project_is_hidden(self):
        self.assertEqual(self.check(), (True, "stale"))

    def test_recent_project_is_visible(self):
        self.assertEqual(self.check(project={"last_session": self.now - 10 * DAY_MS}), (False, None))

    def test_manual_hide_wins(self):
        self.assertEqual(self.check(manual_hidden=True), (True, "manual"))

    def test_favorite_overrides_stale(self):
        self.assertEqual(self.check(favorite=True), (False, None))

    def test_active_project_is_not_stale_hidden(self):
        self.assertEqual(self.check(project={"busy_sessions": 1}), (False, None))
        self.assertEqual(self.check(project={"pending_permissions": 1}), (False, None))
        self.assertEqual(self.check(project={"pending_questions": 1}), (False, None))

    def test_disabled_threshold(self):
        self.assertEqual(self.check(hide_stale_days=0), (False, None))

    def test_other_bucket_is_never_hidden(self):
        self.assertEqual(self.check(project={"kind": "other"}), (False, None))

    def test_missing_activity_is_visible(self):
        self.assertEqual(self.check(project={"last_session": 0, "updated": 0}), (False, None))


if __name__ == "__main__":
    unittest.main()
