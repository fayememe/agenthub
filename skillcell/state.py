from __future__ import annotations

from .models import JobStatus, ProposalStatus, ReviewDecision


class InvalidTransition(ValueError):
    def __init__(self, subject: str, current: str, target: str):
        super().__init__(f"invalid {subject} transition: {current} -> {target}")
        self.subject = subject
        self.current = current
        self.target = target


JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.COLLECTING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.COLLECTING: frozenset(
        {
            JobStatus.ANALYZING,
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.ANALYZING: frozenset(
        {JobStatus.COMPLETED, JobStatus.PARTIAL, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.PARTIAL: frozenset(
        {JobStatus.ANALYZING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


PROPOSAL_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.OBSERVED: frozenset(
        {ProposalStatus.CANDIDATE, ProposalStatus.DEFERRED, ProposalStatus.REJECTED}
    ),
    ProposalStatus.CANDIDATE: frozenset(
        {ProposalStatus.DRAFT, ProposalStatus.DEFERRED, ProposalStatus.REJECTED}
    ),
    ProposalStatus.DEFERRED: frozenset(
        {ProposalStatus.CANDIDATE, ProposalStatus.DRAFT, ProposalStatus.REJECTED}
    ),
    ProposalStatus.DRAFT: frozenset(
        {ProposalStatus.TESTED, ProposalStatus.DEFERRED, ProposalStatus.REJECTED}
    ),
    ProposalStatus.TESTED: frozenset(
        {ProposalStatus.APPROVED, ProposalStatus.DRAFT, ProposalStatus.REJECTED}
    ),
    ProposalStatus.APPROVED: frozenset(
        {ProposalStatus.PUBLISHED, ProposalStatus.DEFERRED, ProposalStatus.REJECTED}
    ),
    ProposalStatus.PUBLISHED: frozenset({ProposalStatus.DRAFT, ProposalStatus.DEPRECATED}),
    ProposalStatus.REJECTED: frozenset({ProposalStatus.CANDIDATE}),
    ProposalStatus.DEPRECATED: frozenset({ProposalStatus.CANDIDATE}),
}


REVIEW_TARGETS: dict[ReviewDecision, ProposalStatus] = {
    ReviewDecision.PROMOTE: ProposalStatus.CANDIDATE,
    ReviewDecision.CREATE_DRAFT: ProposalStatus.DRAFT,
    ReviewDecision.IMPROVE_EXISTING: ProposalStatus.DRAFT,
    ReviewDecision.DEFER: ProposalStatus.DEFERRED,
    ReviewDecision.REJECT: ProposalStatus.REJECTED,
    ReviewDecision.APPROVE: ProposalStatus.APPROVED,
    ReviewDecision.PUBLISH: ProposalStatus.PUBLISHED,
    ReviewDecision.DEPRECATE: ProposalStatus.DEPRECATED,
    ReviewDecision.REOPEN: ProposalStatus.CANDIDATE,
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target not in JOB_TRANSITIONS[current]:
        raise InvalidTransition("job", current.value, target.value)
    return target


def transition_proposal(current: ProposalStatus, target: ProposalStatus) -> ProposalStatus:
    if target not in PROPOSAL_TRANSITIONS[current]:
        raise InvalidTransition("proposal", current.value, target.value)
    return target


def status_for_review(current: ProposalStatus, decision: ReviewDecision) -> ProposalStatus:
    target = REVIEW_TARGETS[decision]
    return transition_proposal(current, target)
