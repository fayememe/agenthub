from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skillcell.repository import FilesystemRepository


def skill_md(name: str, description: str = "Demo skill", body: str = "Use it.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n"


class FilesystemRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.live = root / "live"
        self.state = root / "state"
        self.repo = FilesystemRepository(self.live, self.state)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stage_validate_publish_and_list(self):
        staged = self.repo.stage(
            "demo",
            {
                "SKILL.md": skill_md("demo"),
                "references/guide.md": "details\n",
            },
        )
        self.assertTrue(self.repo.validate(staged.id).ok)
        self.assertEqual([item.change for item in self.repo.diff(staged.id)], ["added", "added"])

        artifact = self.repo.publish(staged.id)
        self.assertEqual(artifact.name, "demo")
        self.assertEqual(artifact.description, "Demo skill")
        self.assertIn("SKILL.md", artifact.files)
        self.assertTrue((self.live / "demo" / "SKILL.md").is_file())
        self.assertEqual([item.name for item in self.repo.list()], ["demo"])
        self.assertEqual(self.repo.history("demo"), [])
        self.assertFalse((self.state / "staging" / staged.id).exists())

    def test_publish_keeps_previous_version(self):
        first = self.repo.stage("demo", {"SKILL.md": skill_md("demo", body="old")})
        first_artifact = self.repo.publish(first.id)

        second = self.repo.stage("demo", {"SKILL.md": skill_md("demo", body="new")})
        diffs = self.repo.diff(second.id)
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].change, "modified")
        self.assertIn("old", diffs[0].old)
        self.assertIn("new", diffs[0].new)
        second_artifact = self.repo.publish(second.id)

        self.assertNotEqual(first_artifact.content_hash, second_artifact.content_hash)
        history = self.repo.history("demo")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].content_hash, first_artifact.content_hash)
        self.assertIn("old", (history[0].path / "SKILL.md").read_text())

    def test_stage_survives_repository_restart(self):
        staged = self.repo.stage("demo", {"SKILL.md": skill_md("demo")})
        reopened = FilesystemRepository(self.live, self.state)
        artifact = reopened.publish(staged.id)
        self.assertEqual(artifact.name, "demo")

    def test_validation_rejects_bad_frontmatter(self):
        staged = self.repo.stage(
            "demo",
            {"SKILL.md": "---\nname: other\ndescription: wrong\n---\n"},
        )
        result = self.repo.validate(staged.id)
        self.assertFalse(result.ok)
        self.assertTrue(any("does not match" in issue.message for issue in result.errors))
        with self.assertRaises(ValueError):
            self.repo.publish(staged.id)

    def test_validation_detects_tampered_stage(self):
        staged = self.repo.stage("demo", {"SKILL.md": skill_md("demo")})
        (staged.path / "SKILL.md").write_text(skill_md("demo", body="tampered"))
        result = self.repo.validate(staged.id)
        self.assertFalse(result.ok)
        self.assertTrue(any("content hash" in issue.message for issue in result.errors))

    def test_validation_requires_skill_md(self):
        staged = self.repo.stage("demo", {"notes.md": "hello\n"})
        result = self.repo.validate(staged.id)
        self.assertFalse(result.ok)
        self.assertTrue(any(issue.path == "SKILL.md" for issue in result.errors))

    def test_validation_warns_about_secrets(self):
        staged = self.repo.stage(
            "demo",
            {
                "SKILL.md": skill_md("demo"),
                "references/leak.md": "token sk-abcdefghijklmnopqrstuvwx\n",
            },
        )
        result = self.repo.validate(staged.id)
        self.assertTrue(result.ok)
        self.assertTrue(any("possible secret" in issue.message for issue in result.warnings))

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.stage("demo", {"../escape.md": "no"})
        with self.assertRaises(ValueError):
            self.repo.stage("demo", {"/absolute.md": "no"})
        with self.assertRaises(ValueError):
            self.repo.stage("demo", {"dir\\file.md": "no"})
        with self.assertRaises(ValueError):
            self.repo.stage("demo", {".git/config": "no"})

    def test_invalid_skill_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.repo.stage("Demo", {"SKILL.md": skill_md("Demo")})
        with self.assertRaises(ValueError):
            self.repo.get("../demo")

    def test_list_skips_directories_without_skill_md(self):
        (self.live / "empty").mkdir()
        staged = self.repo.stage("demo", {"SKILL.md": skill_md("demo")})
        self.repo.publish(staged.id)
        self.assertEqual([item.name for item in self.repo.list()], ["demo"])

    def test_stage_requires_files(self):
        with self.assertRaises(ValueError):
            self.repo.stage("demo", {})


if __name__ == "__main__":
    unittest.main()
