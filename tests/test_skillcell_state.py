from __future__ import annotations

import unittest

from skillcell.models import JobStatus, ProposalStatus, ReviewDecision
from skillcell.state import (
    InvalidTransition,
    status_for_review,
    transition_job,
    transition_proposal,
)


class JobStateTests(unittest.TestCase):
    def test_valid_path(self):
        self.assertEqual(
            transition_job(JobStatus.QUEUED, JobStatus.COLLECTING),
            JobStatus.COLLECTING,
        )
        self.assertEqual(
            transition_job(JobStatus.COLLECTING, JobStatus.ANALYZING),
            JobStatus.ANALYZING,
        )
        self.assertEqual(
            transition_job(JobStatus.ANALYZING, JobStatus.COMPLETED),
            JobStatus.COMPLETED,
        )

    def test_failed_job_can_retry(self):
        self.assertEqual(
            transition_job(JobStatus.FAILED, JobStatus.QUEUED),
            JobStatus.QUEUED,
        )

    def test_terminal_job_cannot_restart(self):
        with self.assertRaises(InvalidTransition):
            transition_job(JobStatus.COMPLETED, JobStatus.QUEUED)


class ProposalStateTests(unittest.TestCase):
    def test_review_lifecycle(self):
        status = ProposalStatus.OBSERVED
        status = status_for_review(status, ReviewDecision.PROMOTE)
        self.assertEqual(status, ProposalStatus.CANDIDATE)
        status = status_for_review(status, ReviewDecision.CREATE_DRAFT)
        self.assertEqual(status, ProposalStatus.DRAFT)
        status = transition_proposal(status, ProposalStatus.TESTED)
        self.assertEqual(status, ProposalStatus.TESTED)
        status = status_for_review(status, ReviewDecision.APPROVE)
        self.assertEqual(status, ProposalStatus.APPROVED)
        status = status_for_review(status, ReviewDecision.PUBLISH)
        self.assertEqual(status, ProposalStatus.PUBLISHED)
        status = status_for_review(status, ReviewDecision.DEPRECATE)
        self.assertEqual(status, ProposalStatus.DEPRECATED)

    def test_published_skill_can_be_revised(self):
        status = transition_proposal(ProposalStatus.PUBLISHED, ProposalStatus.DRAFT)
        self.assertEqual(status, ProposalStatus.DRAFT)

    def test_observed_and_approved_can_be_deferred(self):
        self.assertEqual(
            status_for_review(ProposalStatus.OBSERVED, ReviewDecision.DEFER),
            ProposalStatus.DEFERRED,
        )
        self.assertEqual(
            status_for_review(ProposalStatus.APPROVED, ReviewDecision.DEFER),
            ProposalStatus.DEFERRED,
        )

    def test_rejected_proposal_can_reopen(self):
        status = status_for_review(ProposalStatus.REJECTED, ReviewDecision.REOPEN)
        self.assertEqual(status, ProposalStatus.CANDIDATE)

    def test_cannot_draft_observed_proposal_directly(self):
        with self.assertRaises(InvalidTransition):
            status_for_review(ProposalStatus.OBSERVED, ReviewDecision.CREATE_DRAFT)


if __name__ == "__main__":
    unittest.main()
