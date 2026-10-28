from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .clock import now_ms
from .models import (
    ArtifactType,
    Evidence,
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    Scope,
    SourceRef,
)
from .opencode import OpenCodeError
from .reasoner import OpenCodeReasoner
from .source import OpenCodeSource
from .store import CellStore


SourceResolver = Callable[[str], Awaitable[tuple[OpenCodeSource, SourceRef]]]


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    lookback_ms: int = 7 * 24 * 3600 * 1000
    overlap_ms: int = 6 * 3600 * 1000
    max_sessions: int = 10
    max_session_pages: int = 5
    message_limit: int = 50
    max_evidence: int = 40
    min_score: float = 60.0
    max_proposals: int = 5
    skip_title_prefixes: tuple[str, ...] = ("skillcell:",)


def _normalize_title(value: str) -> str:
    return " ".join(value.lower().strip().split())


def _as_str_list(value: Any, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items = [str(item).strip() for item in value if str(item).strip()]
    return tuple(items[:limit])


class AuditPipeline:
    def __init__(
        self,
        store: CellStore,
        reasoner: OpenCodeReasoner,
        resolve_source: SourceResolver,
        settings: PipelineSettings | None = None,
    ):
        self.store = store
        self.reasoner = reasoner
        self.resolve_source = resolve_source
        self.settings = settings or PipelineSettings()

    async def run_job(self, job: Job) -> Job:
        self.store.transition_job(job.id, JobStatus.COLLECTING)
        try:
            provider, source = await self.resolve_source(job.source_id)
            since_ms = self._since(source.source_id)
            page_size = max(50, self.settings.max_sessions)
            sessions = await provider.recent_sessions(
                source,
                since_ms,
                page_size=page_size,
                max_pages=self.settings.max_session_pages,
            )
            sessions = [
                item
                for item in sessions
                if not any(
                    item.title.lower().startswith(prefix)
                    for prefix in self.settings.skip_title_prefixes
                )
            ][: self.settings.max_sessions]

            new_evidence = 0
            for session in sessions:
                evidence = await provider.read_session_evidence(
                    source,
                    session.id,
                    message_limit=self.settings.message_limit,
                )
                for item in evidence:
                    if self.store.add_evidence(item):
                        new_evidence += 1

            recent = list(
                reversed(
                    self.store.list_evidence(
                        source_id=source.source_id,
                        limit=self.settings.max_evidence,
                    )
                )
            )
            if not recent or (not sessions and not new_evidence):
                self._finish(job, source, sessions, len(recent), 0)
                return self.store.get_job(job.id) or job

            self.store.transition_job(job.id, JobStatus.ANALYZING)
            existing_skills: Sequence[Mapping[str, Any]] = []
            try:
                existing_skills = await provider.client.skills()
            except OpenCodeError:
                existing_skills = []
            result = await self.reasoner.propose(
                recent, existing_skills, source, focus=job.focus
            )
            if result.session_id:
                self.store.update_job_session(job.id, result.session_id)
            proposals = self._build_proposals(
                result.proposals,
                recent,
                source,
                existing_skills,
            )
            self.store.update_job_proposals(job.id, [item.id for item in proposals])
            self._finish(job, source, sessions, len(recent), len(proposals))
            return self.store.get_job(job.id) or job
        except Exception as exc:
            try:
                self.store.transition_job(
                    job.id, JobStatus.FAILED, error=str(exc)[:500] or exc.__class__.__name__
                )
            except Exception:
                pass
            return self.store.get_job(job.id) or job

    def _since(self, source_id: str) -> int:
        cursor = self.store.get_cursor(source_id)
        if cursor is None:
            return max(0, now_ms() - self.settings.lookback_ms)
        scanned_at = cursor.updated_at
        try:
            marker = json.loads(cursor.marker)
        except (TypeError, json.JSONDecodeError):
            marker = {}
        if isinstance(marker, dict) and isinstance(marker.get("scanned_at"), (int, float)):
            scanned_at = int(marker["scanned_at"])
        return max(0, scanned_at - self.settings.overlap_ms)

    def _finish(
        self,
        job: Job,
        source: SourceRef,
        sessions: Sequence[Any],
        evidence_count: int,
        proposal_count: int,
    ) -> None:
        latest = max((item.updated_at for item in sessions), default=0)
        marker = {
            "scanned_at": now_ms(),
            "latest_session_updated": latest,
            "sessions": len(sessions),
            "evidence": evidence_count,
            "proposals": proposal_count,
        }
        self.store.set_cursor(source.source_id, json.dumps(marker, ensure_ascii=False))
        self.store.transition_job(job.id, JobStatus.COMPLETED)

    def _build_proposals(
        self,
        drafts: Sequence[Mapping[str, Any]],
        evidence: Sequence[Evidence],
        source: SourceRef,
        existing_skills: Sequence[Mapping[str, Any]],
    ) -> list[Proposal]:
        existing_titles = {
            _normalize_title(item.title)
            for item in self.store.list_proposals(limit=1000)
        }
        proposals: list[Proposal] = []
        for draft in drafts[: self.settings.max_proposals]:
            title = str(draft.get("title") or "").strip()
            if not title:
                continue
            normalized = _normalize_title(title)
            if not normalized or normalized in existing_titles:
                continue
            try:
                score = float(draft.get("score"))
            except (TypeError, ValueError):
                continue
            score = min(100.0, max(0.0, score))
            if score < self.settings.min_score:
                continue
            try:
                artifact_type = ArtifactType(str(draft.get("artifact_type") or "skill"))
            except ValueError:
                artifact_type = ArtifactType.SKILL
            try:
                scope = Scope(str(draft.get("scope") or "personal"))
            except ValueError:
                scope = Scope.PERSONAL
            evidence_refs = []
            evidence_ids: list[str] = []
            raw_ids = draft.get("evidence_ids")
            if isinstance(raw_ids, list):
                for evidence_id in raw_ids:
                    item = self.store.get_evidence(str(evidence_id))
                    if item is not None:
                        evidence_refs.append(item.ref)
                        evidence_ids.append(item.id)
            if not evidence_refs:
                evidence_refs = [item.ref for item in evidence[:5]]
                evidence_ids = [item.id for item in evidence[:5]]
            existing_skill = draft.get("existing_skill")
            if not isinstance(existing_skill, str) or not existing_skill.strip():
                existing_skill = self._match_existing_skill(title, existing_skills)
            else:
                existing_skill = existing_skill.strip()
            proposal_id = "prop_" + hashlib.sha256(
                f"{source.source_id}|{artifact_type.value}|{normalized}".encode("utf-8")
            ).hexdigest()[:32]
            at = now_ms()
            proposal = Proposal(
                id=proposal_id,
                artifact_type=artifact_type,
                title=title[:300],
                scope=scope,
                score=score,
                status=ProposalStatus.OBSERVED,
                created_at=at,
                updated_at=at,
                summary=str(draft.get("summary") or "")[:4000],
                problem=str(draft.get("problem") or "")[:4000],
                triggers=_as_str_list(draft.get("triggers")),
                exclusions=_as_str_list(draft.get("exclusions")),
                evidence_refs=tuple(evidence_refs),
                evidence_ids=tuple(evidence_ids),
                source_refs=(source,),
                existing_skill=existing_skill,
            )
            try:
                self.store.create_proposal(proposal)
            except sqlite3.IntegrityError:
                continue
            existing_titles.add(normalized)
            proposals.append(proposal)
        return proposals

    def _match_existing_skill(
        self,
        title: str,
        existing_skills: Sequence[Mapping[str, Any]],
    ) -> str | None:
        normalized = _normalize_title(title)
        for skill in existing_skills:
            name = str(skill.get("name") or "").strip()
            if not name:
                continue
            candidate = _normalize_title(name)
            if candidate and (candidate in normalized or normalized in candidate):
                return name
        return None
