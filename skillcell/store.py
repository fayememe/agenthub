from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .clock import now_ms
from .models import (
    ArtifactType,
    Cursor,
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
from .state import status_for_review, transition_job, transition_proposal


SCHEMA = """
CREATE TABLE IF NOT EXISTS cell_schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_job (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    started_at INTEGER,
    finished_at INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    session_id TEXT,
    proposal_ids TEXT NOT NULL DEFAULT '[]',
    focus TEXT
);
CREATE INDEX IF NOT EXISTS cell_job_status_idx ON cell_job(status, updated_at);

CREATE TABLE IF NOT EXISTS cell_proposal (
    id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    title TEXT NOT NULL,
    scope TEXT NOT NULL,
    score REAL NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    problem TEXT NOT NULL DEFAULT '',
    triggers TEXT NOT NULL DEFAULT '[]',
    exclusions TEXT NOT NULL DEFAULT '[]',
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    existing_skill TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cell_proposal_status_idx ON cell_proposal(status, updated_at);

CREATE TABLE IF NOT EXISTS cell_review (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cell_review_proposal_idx ON cell_review(proposal_id, created_at);

CREATE TABLE IF NOT EXISTS cell_evidence (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_id TEXT,
    part_id TEXT,
    kind TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    privacy_class TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS cell_evidence_session_idx ON cell_evidence(source_id, session_id);

CREATE TABLE IF NOT EXISTS cell_cursor (
    source_id TEXT PRIMARY KEY,
    marker TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_artifact (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class CellStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            row = self._conn.execute(
                "SELECT version FROM cell_schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO cell_schema_version(version) VALUES (3)"
                )
            elif int(row["version"]) > 3:
                raise RuntimeError(f"unsupported skillcell schema version: {row['version']}")
            columns = {
                str(item["name"])
                for item in self._conn.execute("PRAGMA table_info(cell_proposal)").fetchall()
            }
            if "evidence_ids" not in columns:
                self._conn.execute(
                    "ALTER TABLE cell_proposal ADD COLUMN evidence_ids TEXT NOT NULL DEFAULT '[]'"
                )
            job_columns = {
                str(item["name"])
                for item in self._conn.execute("PRAGMA table_info(cell_job)").fetchall()
            }
            if "focus" not in job_columns:
                self._conn.execute("ALTER TABLE cell_job ADD COLUMN focus TEXT")
            self._conn.execute("UPDATE cell_schema_version SET version = 3")

    def create_job(self, job: Job) -> Job:
        created_at = job.created_at or now_ms()
        updated_at = job.updated_at or created_at
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cell_job(id, source_id, status, created_at, updated_at, "
                "started_at, finished_at, attempts, error, session_id, proposal_ids, focus) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.id,
                    job.source_id,
                    job.status.value,
                    created_at,
                    updated_at,
                    job.started_at,
                    job.finished_at,
                    job.attempts,
                    job.error,
                    job.session_id,
                    _dump(list(job.proposal_ids)),
                    job.focus,
                ),
            )
        return self.get_job(job.id) or job

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cell_job WHERE id = ?", (job_id,)
            ).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(
        self,
        status: JobStatus | None = None,
        limit: int = 100,
    ) -> list[Job]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT * FROM cell_job ORDER BY updated_at DESC, id LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM cell_job WHERE status = ? "
                    "ORDER BY updated_at DESC, id LIMIT ?",
                    (status.value, max(1, limit)),
                ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def transition_job(
        self,
        job_id: str,
        target: JobStatus,
        *,
        error: str | None = None,
        session_id: str | None = None,
    ) -> Job:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM cell_job WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self._job_from_row(row)
            transition_job(current.status, target)
            at = now_ms()
            started_at = current.started_at
            if started_at is None and target == JobStatus.COLLECTING:
                started_at = at
            finished_at = (
                at
                if target in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
                else None
            )
            attempts = current.attempts
            if current.status == JobStatus.QUEUED and target == JobStatus.COLLECTING:
                attempts += 1
            self._conn.execute(
                "UPDATE cell_job SET status = ?, updated_at = ?, started_at = ?, "
                "finished_at = ?, attempts = ?, error = ?, session_id = ? WHERE id = ?",
                (
                    target.value,
                    at,
                    started_at,
                    finished_at,
                    attempts,
                    error,
                    session_id if session_id is not None else current.session_id,
                    job_id,
                ),
            )
        return self.get_job(job_id) or current

    def active_job(self, source_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cell_job WHERE source_id = ? AND status IN "
                "(?, ?, ?, ?) ORDER BY created_at LIMIT 1",
                (
                    source_id,
                    JobStatus.QUEUED.value,
                    JobStatus.COLLECTING.value,
                    JobStatus.ANALYZING.value,
                    JobStatus.PARTIAL.value,
                ),
            ).fetchone()
        return self._job_from_row(row) if row else None

    def update_job_session(self, job_id: str, session_id: str) -> Job:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE cell_job SET session_id = ?, updated_at = ? WHERE id = ?",
                (session_id, now_ms(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def update_job_proposals(self, job_id: str, proposal_ids: Sequence[str]) -> Job:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE cell_job SET proposal_ids = ?, updated_at = ? WHERE id = ?",
                (_dump(list(proposal_ids)), now_ms(), job_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(job_id)
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def create_proposal(self, proposal: Proposal) -> Proposal:
        created_at = proposal.created_at or now_ms()
        updated_at = proposal.updated_at or created_at
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cell_proposal(id, artifact_type, title, scope, score, "
                "status, summary, problem, triggers, exclusions, evidence_refs, "
                "evidence_ids, source_refs, existing_skill, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal.id,
                    proposal.artifact_type.value,
                    proposal.title,
                    proposal.scope.value,
                    proposal.score,
                    proposal.status.value,
                    proposal.summary,
                    proposal.problem,
                    _dump(list(proposal.triggers)),
                    _dump(list(proposal.exclusions)),
                    _dump([ref.to_dict() for ref in proposal.evidence_refs]),
                    _dump(list(proposal.evidence_ids)),
                    _dump([ref.to_dict() for ref in proposal.source_refs]),
                    proposal.existing_skill,
                    created_at,
                    updated_at,
                ),
            )
        return self.get_proposal(proposal.id) or proposal

    def get_proposal(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cell_proposal WHERE id = ?", (proposal_id,)
            ).fetchone()
        return self._proposal_from_row(row) if row else None

    def list_proposals(
        self,
        status: ProposalStatus | None = None,
        limit: int = 100,
    ) -> list[Proposal]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT * FROM cell_proposal ORDER BY updated_at DESC, id LIMIT ?",
                    (max(1, limit),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM cell_proposal WHERE status = ? "
                    "ORDER BY updated_at DESC, id LIMIT ?",
                    (status.value, max(1, limit)),
                ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def transition_proposal(self, proposal_id: str, target: ProposalStatus) -> Proposal:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM cell_proposal WHERE id = ?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            current = self._proposal_from_row(row)
            transition_proposal(current.status, target)
            self._conn.execute(
                "UPDATE cell_proposal SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, now_ms(), proposal_id),
            )
        return self.get_proposal(proposal_id) or current

    def apply_review(self, review: Review) -> Proposal:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM cell_proposal WHERE id = ?", (review.proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(review.proposal_id)
            current = self._proposal_from_row(row)
            target = status_for_review(current.status, review.decision)
            created_at = review.created_at or now_ms()
            self._conn.execute(
                "INSERT INTO cell_review(id, proposal_id, decision, reviewer, "
                "rationale, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    review.id,
                    review.proposal_id,
                    review.decision.value,
                    review.reviewer,
                    review.rationale,
                    created_at,
                ),
            )
            self._conn.execute(
                "UPDATE cell_proposal SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, created_at, review.proposal_id),
            )
        return self.get_proposal(review.proposal_id) or current

    def list_reviews(self, proposal_id: str) -> list[Review]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cell_review WHERE proposal_id = ? ORDER BY created_at, rowid",
                (proposal_id,),
            ).fetchall()
        return [self._review_from_row(row) for row in rows]

    def add_evidence(self, evidence: Evidence) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO cell_evidence(id, source_id, session_id, "
                "message_id, part_id, kind, excerpt, privacy_class, content_hash, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evidence.id,
                    evidence.ref.source_id,
                    evidence.ref.session_id,
                    evidence.ref.message_id,
                    evidence.ref.part_id,
                    evidence.kind.value,
                    evidence.excerpt,
                    evidence.privacy_class.value,
                    evidence.content_hash,
                    evidence.created_at or now_ms(),
                ),
            )
        return cursor.rowcount > 0

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cell_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        return self._evidence_from_row(row) if row else None

    def list_evidence(
        self,
        source_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[Evidence]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, limit))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM cell_evidence{where} ORDER BY created_at DESC, id LIMIT ?",
                params,
            ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM cell_meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cell_meta(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, now_ms()),
            )

    def delete_meta(self, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM cell_meta WHERE key = ?", (key,))

    def get_cursor(self, source_id: str) -> Cursor | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cell_cursor WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            return None
        return Cursor(
            source_id=str(row["source_id"]),
            marker=str(row["marker"]),
            updated_at=int(row["updated_at"]),
        )

    def set_cursor(self, source_id: str, marker: str) -> Cursor:
        at = now_ms()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cell_cursor(source_id, marker, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "marker = excluded.marker, updated_at = excluded.updated_at",
                (source_id, marker, at),
            )
        return Cursor(source_id=source_id, marker=marker, updated_at=at)

    def upsert_artifact(self, artifact: SkillArtifact) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO cell_artifact(name, path, content_hash, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "path = excluded.path, content_hash = excluded.content_hash, "
                "updated_at = excluded.updated_at",
                (
                    artifact.name,
                    str(artifact.path),
                    artifact.content_hash,
                    artifact.updated_at or now_ms(),
                ),
            )

    def list_artifacts(self) -> list[SkillArtifact]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM cell_artifact ORDER BY name"
            ).fetchall()
        return [
            SkillArtifact(
                name=str(row["name"]),
                path=Path(str(row["path"])),
                description="",
                skill_md="",
                files=(),
                content_hash=str(row["content_hash"]),
                updated_at=int(row["updated_at"]),
            )
            for row in rows
        ]

    def _job_from_row(self, row: sqlite3.Row) -> Job:
        proposal_ids = _load(str(row["proposal_ids"]), [])
        return Job(
            id=str(row["id"]),
            source_id=str(row["source_id"]),
            status=JobStatus(str(row["status"])),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            attempts=int(row["attempts"]),
            error=row["error"],
            session_id=row["session_id"],
            proposal_ids=tuple(str(item) for item in proposal_ids),
            focus=row["focus"],
        )

    def _proposal_from_row(self, row: sqlite3.Row) -> Proposal:
        triggers = _load(str(row["triggers"]), [])
        exclusions = _load(str(row["exclusions"]), [])
        evidence_refs = _load(str(row["evidence_refs"]), [])
        evidence_ids = _load(str(row["evidence_ids"]), [])
        source_refs = _load(str(row["source_refs"]), [])
        return Proposal(
            id=str(row["id"]),
            artifact_type=ArtifactType(str(row["artifact_type"])),
            title=str(row["title"]),
            scope=Scope(str(row["scope"])),
            score=float(row["score"]),
            status=ProposalStatus(str(row["status"])),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            summary=str(row["summary"]),
            problem=str(row["problem"]),
            triggers=tuple(str(item) for item in triggers),
            exclusions=tuple(str(item) for item in exclusions),
            evidence_refs=tuple(
                EvidenceRef.from_dict(item) for item in evidence_refs if isinstance(item, dict)
            ),
            evidence_ids=tuple(str(item) for item in evidence_ids),
            source_refs=tuple(
                SourceRef.from_dict(item) for item in source_refs if isinstance(item, dict)
            ),
            existing_skill=row["existing_skill"],
        )

    def _evidence_from_row(self, row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=str(row["id"]),
            ref=EvidenceRef(
                source_id=str(row["source_id"]),
                session_id=str(row["session_id"]),
                message_id=row["message_id"],
                part_id=row["part_id"],
            ),
            kind=EvidenceKind(str(row["kind"])),
            excerpt=str(row["excerpt"]),
            content_hash=str(row["content_hash"]),
            privacy_class=PrivacyClass(str(row["privacy_class"])),
            created_at=int(row["created_at"]),
        )

    def _review_from_row(self, row: sqlite3.Row) -> Review:
        return Review(
            id=str(row["id"]),
            proposal_id=str(row["proposal_id"]),
            decision=ReviewDecision(str(row["decision"])),
            reviewer=str(row["reviewer"]),
            rationale=str(row["rationale"]),
            created_at=int(row["created_at"]),
        )
