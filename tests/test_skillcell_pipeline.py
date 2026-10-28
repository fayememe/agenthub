from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skillcell.models import (
    Evidence,
    EvidenceKind,
    EvidenceRef,
    Job,
    JobStatus,
    PrivacyClass,
    SessionSummary,
    SourceRef,
)
from skillcell.pipeline import AuditPipeline, PipelineSettings
from skillcell.reasoner import ReasonerResult
from skillcell.store import CellStore


def evidence(evidence_id: str, session: str = "ses_1") -> Evidence:
    excerpt = f"evidence {evidence_id}"
    return Evidence(
        id=evidence_id,
        ref=EvidenceRef(source_id="src", session_id=session),
        kind=EvidenceKind.OTHER,
        excerpt=excerpt,
        content_hash=hashlib.sha256(excerpt.encode()).hexdigest(),
        privacy_class=PrivacyClass.PRIVATE,
        created_at=1000,
    )


class FakeSkillsClient:
    def __init__(self) -> None:
        self.calls = 0

    async def skills(self):
        self.calls += 1
        return [
            {"name": "safe-git", "description": "Safe git workflow"},
        ]


class FakeProvider:
    def __init__(self, evidence_items, sessions=None) -> None:
        self.evidence_items = evidence_items
        self.sessions = sessions or [
            SessionSummary(
                id="ses_1",
                source_id="src",
                title="Demo session",
                updated_at=2000,
            )
        ]
        self.client = FakeSkillsClient()
        self.calls: list[dict] = []

    async def recent_sessions(self, source, since_ms, *, page_size, max_pages):
        self.calls.append({"since_ms": since_ms, "page_size": page_size, "max_pages": max_pages})
        return self.sessions

    async def read_session_evidence(self, source, session_id, *, message_limit):
        self.calls.append({"session_id": session_id, "message_limit": message_limit})
        return self.evidence_items


class FakeReasoner:
    def __init__(self, drafts, *, fail: bool = False) -> None:
        self.drafts = drafts
        self.fail = fail
        self.calls: list[dict] = []

    async def propose(self, evidence, existing_skills, source, *, focus=None):
        self.calls.append(
            {
                "evidence": [item.id for item in evidence],
                "skills": list(existing_skills),
                "source": source.source_id,
                "focus": focus,
            }
        )
        if self.fail:
            raise RuntimeError("reasoner exploded")
        return ReasonerResult(proposals=tuple(self.drafts), session_id="ses_audit")


def draft(**overrides):
    value = {
        "title": "Safe git integration",
        "artifact_type": "skill",
        "scope": "personal",
        "score": 86,
        "summary": "A repeatable preflight and recovery workflow.",
        "problem": "Merges sometimes overwrite local work.",
        "triggers": ["remote changed", "local edits"],
        "exclusions": ["public repository"],
        "existing_skill": None,
        "evidence_ids": ["ev_1"],
    }
    value.update(overrides)
    return value


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CellStore(Path(self.tmp.name) / "skillcell.db")
        self.source = SourceRef(
            source_id="src",
            node="node-a",
            project_id="p1",
            directory="/work/demo",
        )
        self.provider = FakeProvider([evidence("ev_1")])
        self.reasoner = FakeReasoner([draft()])
        self.pipeline = AuditPipeline(
            self.store,
            self.reasoner,
            self.resolve,
            PipelineSettings(min_score=60, max_sessions=5),
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def resolve(self, source_id: str):
        return self.provider, self.source

    def job(self, focus: str | None = None) -> Job:
        return self.store.create_job(
            Job(
                id="job-1",
                source_id="src",
                status=JobStatus.QUEUED,
                created_at=0,
                updated_at=0,
                focus=focus,
            )
        )

    async def test_scan_creates_proposal_and_cursor(self):
        job = await self.pipeline.run_job(self.job())
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.session_id, "ses_audit")
        self.assertEqual(len(job.proposal_ids), 1)
        proposals = self.store.list_proposals()
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.title, "Safe git integration")
        self.assertEqual(proposal.score, 86)
        self.assertEqual(proposal.evidence_ids, ("ev_1",))
        self.assertEqual(proposal.existing_skill, None)
        self.assertEqual(self.store.list_evidence(), [self.store.get_evidence("ev_1")])
        cursor = self.store.get_cursor("src")
        self.assertIsNotNone(cursor)
        marker = json.loads(cursor.marker)
        self.assertEqual(marker["sessions"], 1)
        self.assertEqual(marker["proposals"], 1)
        self.assertEqual(len(self.reasoner.calls), 1)
        self.assertEqual(self.reasoner.calls[0]["skills"][0]["name"], "safe-git")

    async def test_focus_is_passed_to_reasoner(self):
        await self.pipeline.run_job(self.job(focus="release hardening full-send mode"))
        self.assertEqual(self.reasoner.calls[0]["focus"], "release hardening full-send mode")

    async def test_duplicate_title_is_not_recreated(self):
        await self.pipeline.run_job(self.job())
        second = self.store.create_job(
            Job(
                id="job-2",
                source_id="src",
                status=JobStatus.QUEUED,
                created_at=0,
                updated_at=0,
            )
        )
        result = await self.pipeline.run_job(second)
        self.assertEqual(result.status, JobStatus.COMPLETED)
        self.assertEqual(len(self.store.list_proposals()), 1)

    async def test_reasoner_failure_marks_job_failed_without_cursor(self):
        self.reasoner.fail = True
        job = await self.pipeline.run_job(self.job())
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("reasoner exploded", job.error or "")
        self.assertIsNone(self.store.get_cursor("src"))
        self.assertEqual(len(self.store.list_evidence()), 1)

    async def test_no_evidence_completes_without_reasoner(self):
        self.provider.evidence_items = []
        job = await self.pipeline.run_job(self.job())
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(self.reasoner.calls, [])
        self.assertIsNotNone(self.store.get_cursor("src"))

    async def test_low_score_is_skipped(self):
        self.reasoner.drafts = [draft(score=40)]
        job = await self.pipeline.run_job(self.job())
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(self.store.list_proposals(), [])

    async def test_audit_session_is_skipped(self):
        self.provider.sessions = [
            SessionSummary(
                id="ses_audit",
                source_id="src",
                title="skillcell: src",
                updated_at=2000,
            )
        ]
        job = await self.pipeline.run_job(self.job())
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(self.reasoner.calls, [])
        self.assertEqual(self.store.list_evidence(), [])

    async def test_existing_skill_match(self):
        self.reasoner.drafts = [draft(title="safe-git improvements")]
        await self.pipeline.run_job(self.job())
        proposal = self.store.list_proposals()[0]
        self.assertEqual(proposal.existing_skill, "safe-git")


if __name__ == "__main__":
    unittest.main()
