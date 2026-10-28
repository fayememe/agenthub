from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ArtifactType(str, Enum):
    SKILL = "skill"
    RULE = "rule"
    WORKFLOW = "workflow"
    MCP_TOOL = "mcp_tool"
    REFERENCE = "reference"


class Scope(str, Enum):
    PROJECT = "project"
    PERSONAL = "personal"
    SHARED = "shared"


class JobStatus(str, Enum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    ANALYZING = "analyzing"
    PARTIAL = "partial"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProposalStatus(str, Enum):
    OBSERVED = "observed"
    CANDIDATE = "candidate"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    DRAFT = "draft"
    TESTED = "tested"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"


class ReviewDecision(str, Enum):
    PROMOTE = "promote"
    CREATE_DRAFT = "create_draft"
    IMPROVE_EXISTING = "improve_existing"
    DEFER = "defer"
    REJECT = "reject"
    APPROVE = "approve"
    PUBLISH = "publish"
    DEPRECATE = "deprecate"
    REOPEN = "reopen"


class EvidenceKind(str, Enum):
    USER_CORRECTION = "user_correction"
    TOOL_ERROR = "tool_error"
    SUCCESSFUL_PATTERN = "successful_pattern"
    REPEATED_STEPS = "repeated_steps"
    SKILL_GAP = "skill_gap"
    OTHER = "other"


class PrivacyClass(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    node: str | None = None
    project_id: str | None = None
    directory: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "node": self.node,
            "project_id": self.project_id,
            "directory": self.directory,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceRef:
        return cls(
            source_id=str(value["source_id"]),
            node=value.get("node"),
            project_id=value.get("project_id"),
            directory=value.get("directory"),
        )


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_id: str
    session_id: str
    message_id: str | None = None
    part_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "part_id": self.part_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceRef:
        return cls(
            source_id=str(value["source_id"]),
            session_id=str(value["session_id"]),
            message_id=value.get("message_id"),
            part_id=value.get("part_id"),
        )


@dataclass(frozen=True, slots=True)
class Evidence:
    id: str
    ref: EvidenceRef
    kind: EvidenceKind
    excerpt: str
    content_hash: str
    privacy_class: PrivacyClass = PrivacyClass.PRIVATE
    created_at: int = 0

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("evidence id is required")
        if not self.ref.session_id:
            raise ValueError("evidence session_id is required")
        if not self.content_hash:
            raise ValueError("evidence content_hash is required")


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    artifact_type: ArtifactType
    title: str
    scope: Scope
    score: float
    status: ProposalStatus
    created_at: int
    updated_at: int
    summary: str = ""
    problem: str = ""
    triggers: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    existing_skill: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("proposal id is required")
        if not self.title.strip():
            raise ValueError("proposal title is required")
        if not 0 <= self.score <= 100:
            raise ValueError("proposal score must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class Review:
    id: str
    proposal_id: str
    decision: ReviewDecision
    reviewer: str
    created_at: int
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.proposal_id:
            raise ValueError("review id and proposal_id are required")
        if not self.reviewer.strip():
            raise ValueError("review reviewer is required")


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    source_id: str
    status: JobStatus
    created_at: int
    updated_at: int
    started_at: int | None = None
    finished_at: int | None = None
    attempts: int = 0
    error: str | None = None
    session_id: str | None = None
    proposal_ids: tuple[str, ...] = ()
    focus: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.source_id:
            raise ValueError("job id and source_id are required")
        if self.attempts < 0:
            raise ValueError("job attempts cannot be negative")


@dataclass(frozen=True, slots=True)
class Cursor:
    source_id: str
    marker: str
    updated_at: int

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("cursor source_id is required")


@dataclass(frozen=True, slots=True)
class SkillArtifact:
    name: str
    path: Path
    description: str
    skill_md: str
    files: tuple[str, ...]
    content_hash: str
    updated_at: int


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    id: str
    name: str
    path: Path
    files: tuple[str, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    name: str
    version_id: str
    path: Path
    content_hash: str
    created_at: int


@dataclass(frozen=True, slots=True)
class FileDiff:
    path: str
    change: str
    old: str = ""
    new: str = ""


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: str
    source_id: str
    title: str
    updated_at: int
    project_id: str | None = None
    directory: str | None = None


@dataclass(frozen=True, slots=True)
class SessionPage:
    sessions: tuple[SessionSummary, ...] = ()
    cursor: str | None = None
    has_more: bool = False


@dataclass(frozen=True, slots=True)
class ReasonerRequest:
    prompt: str
    system: str | None = None
    schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors
