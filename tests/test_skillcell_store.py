from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from skillcell.models import (
    ArtifactType,
    Evidence,
    EvidenceKind,
    EvidenceRef,
    Job,
    JobStatus,
    PrivacyClass,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    Scope,
    SkillArtifact,
    SourceRef,
)
from skillcell.state import InvalidTransition
from skillcell.store import CellStore


def proposal(status: ProposalStatus = ProposalStatus.OBSERVED) -> Proposal:
    return Proposal(
        id="prop-1",
        artifact_type=ArtifactType.SKILL,
        title="Safe git integration",
        scope=Scope.PERSONAL,
        score=86,
        status=status,
        created_at=0,
        updated_at=0,
        summary="A repeatable preflight and recovery workflow.",
        problem="Merges sometimes overwrite local work.",
        triggers=("remote changed", "local edits"),
        exclusions=("public repository",),
        evidence_refs=(EvidenceRef(source_id="src-1", session_id="ses-1"),),
        source_refs=(SourceRef(source_id="src-1", node="node-a"),),
    )


def review(decision: ReviewDecision) -> Review:
    return Review(
        id=uuid.uuid4().hex,
        proposal_id="prop-1",
        decision=decision,
        reviewer="reviewer",
        created_at=0,
    )


class CellStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state" / "cell.db"
        self.store = CellStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_job_lifecycle(self):
        job = Job(
            id="job-1",
            source_id="src-1",
            status=JobStatus.QUEUED,
            created_at=0,
            updated_at=0,
        )
        self.store.create_job(job)
        self.store.transition_job("job-1", JobStatus.COLLECTING)
        self.store.transition_job("job-1", JobStatus.ANALYZING, session_id="ses-audit")
        done = self.store.transition_job("job-1", JobStatus.COMPLETED)
        self.assertEqual(done.status, JobStatus.COMPLETED)
        self.assertEqual(done.attempts, 1)
        self.assertIsNotNone(done.started_at)
        self.assertIsNotNone(done.finished_at)
        self.assertEqual(done.session_id, "ses-audit")
        with self.assertRaises(InvalidTransition):
            self.store.transition_job("job-1", JobStatus.QUEUED)

    def test_job_focus_roundtrip(self):
        self.store.create_job(
            Job(
                id="job-focus",
                source_id="src-1",
                status=JobStatus.QUEUED,
                created_at=0,
                updated_at=0,
                focus="release hardening",
            )
        )
        job = self.store.get_job("job-focus")
        self.assertIsNotNone(job)
        self.assertEqual(job.focus, "release hardening")

    def test_failed_job_can_requeue(self):
        self.store.create_job(
            Job(
                id="job-1",
                source_id="src-1",
                status=JobStatus.QUEUED,
                created_at=0,
                updated_at=0,
            )
        )
        self.store.transition_job("job-1", JobStatus.FAILED, error="boom")
        retried = self.store.transition_job("job-1", JobStatus.QUEUED)
        self.assertEqual(retried.status, JobStatus.QUEUED)
        self.assertIsNone(retried.error)
        self.assertIsNone(retried.finished_at)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_proposal_review_lifecycle(self):
        self.store.create_proposal(proposal())
        self.store.apply_review(review(ReviewDecision.PROMOTE))
        self.store.apply_review(review(ReviewDecision.CREATE_DRAFT))
        self.store.transition_proposal("prop-1", ProposalStatus.TESTED)
        self.store.apply_review(review(ReviewDecision.APPROVE))
        published = self.store.apply_review(review(ReviewDecision.PUBLISH))

        self.assertEqual(published.status, ProposalStatus.PUBLISHED)
        self.assertEqual(published.score, 86)
        self.assertEqual(published.triggers, ("remote changed", "local edits"))
        self.assertEqual(published.evidence_refs[0].session_id, "ses-1")
        decisions = [item.decision for item in self.store.list_reviews("prop-1")]
        self.assertEqual(
            decisions,
            [
                ReviewDecision.PROMOTE,
                ReviewDecision.CREATE_DRAFT,
                ReviewDecision.APPROVE,
                ReviewDecision.PUBLISH,
            ],
        )

    def test_invalid_review_is_rejected(self):
        self.store.create_proposal(proposal())
        with self.assertRaises(InvalidTransition):
            self.store.apply_review(review(ReviewDecision.CREATE_DRAFT))

    def test_cursor_roundtrip(self):
        self.assertIsNone(self.store.get_cursor("src-1"))
        self.store.set_cursor("src-1", "cursor-a")
        self.store.set_cursor("src-1", "cursor-b")
        cursor = self.store.get_cursor("src-1")
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor.marker, "cursor-b")

    def test_evidence_dedup_and_filtering(self):
        evidence = Evidence(
            id="ev-1",
            ref=EvidenceRef(
                source_id="src-1",
                session_id="ses-1",
                message_id="msg-1",
                part_id="prt-1",
            ),
            kind=EvidenceKind.USER_CORRECTION,
            excerpt="Always check the remote before merging.",
            content_hash="a" * 64,
            privacy_class=PrivacyClass.PRIVATE,
        )
        self.assertTrue(self.store.add_evidence(evidence))
        self.assertFalse(self.store.add_evidence(evidence))
        self.store.add_evidence(
            Evidence(
                id="ev-2",
                ref=EvidenceRef(source_id="src-1", session_id="ses-2"),
                kind=EvidenceKind.TOOL_ERROR,
                excerpt="command failed",
                content_hash="b" * 64,
            )
        )
        self.assertEqual(len(self.store.list_evidence()), 2)
        self.assertEqual(len(self.store.list_evidence(session_id="ses-1")), 1)
        loaded = self.store.get_evidence("ev-1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.ref.message_id, "msg-1")
        self.assertEqual(loaded.kind, EvidenceKind.USER_CORRECTION)

    def test_artifact_index_upsert(self):
        artifact = SkillArtifact(
            name="demo",
            path=Path("/skills/demo"),
            description="",
            skill_md="",
            files=(),
            content_hash="a" * 64,
            updated_at=0,
        )
        self.store.upsert_artifact(artifact)
        artifact = SkillArtifact(
            name="demo",
            path=Path("/skills/demo"),
            description="",
            skill_md="",
            files=(),
            content_hash="b" * 64,
            updated_at=0,
        )
        self.store.upsert_artifact(artifact)
        items = self.store.list_artifacts()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].content_hash, "b" * 64)

    def test_data_survives_reopen(self):
        self.store.create_proposal(proposal())
        self.store.set_cursor("src-1", "cursor-a")
        self.store.close()
        self.store = CellStore(self.path)
        self.assertIsNotNone(self.store.get_proposal("prop-1"))
        self.assertIsNotNone(self.store.get_cursor("src-1"))


if __name__ == "__main__":
    unittest.main()
