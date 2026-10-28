from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

import aiohttp

from app.config import HubConfig, NodeConfig
from skillcell.models import (
    Job,
    JobStatus,
    Proposal,
    ProposalStatus,
    Review,
    ReviewDecision,
    SourceRef,
)
from skillcell.chat import ChatService
from skillcell.opencode import OpenCodeClient
from skillcell.pipeline import AuditPipeline, PipelineSettings
from skillcell.reasoner import OpenCodeReasoner
from skillcell.source import OpenCodeSource
from skillcell.store import CellStore
from skillcell.worker import SkillcellWorker

from .config import SkillcellSettings


def source_to_dict(source: SourceRef) -> dict[str, Any]:
    return {
        "sourceID": source.source_id,
        "node": source.node,
        "projectID": source.project_id,
        "directory": source.directory,
    }


def proposal_to_dict(proposal: Proposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "type": proposal.artifact_type.value,
        "title": proposal.title,
        "scope": proposal.scope.value,
        "score": proposal.score,
        "status": proposal.status.value,
        "summary": proposal.summary,
        "problem": proposal.problem,
        "triggers": list(proposal.triggers),
        "exclusions": list(proposal.exclusions),
        "existingSkill": proposal.existing_skill,
        "evidenceCount": len(proposal.evidence_ids) or len(proposal.evidence_refs),
        "createdAt": proposal.created_at,
        "updatedAt": proposal.updated_at,
    }


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "sourceID": job.source_id,
        "status": job.status.value,
        "attempts": job.attempts,
        "createdAt": job.created_at,
        "updatedAt": job.updated_at,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "error": job.error,
        "sessionID": job.session_id,
        "proposalIDs": list(job.proposal_ids),
        "focus": job.focus,
    }


def evidence_to_dict(evidence: Any) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "sourceID": evidence.ref.source_id,
        "sessionID": evidence.ref.session_id,
        "messageID": evidence.ref.message_id,
        "partID": evidence.ref.part_id,
        "kind": evidence.kind.value,
        "excerpt": evidence.excerpt,
        "privacyClass": evidence.privacy_class.value,
        "createdAt": evidence.created_at,
    }


class SkillcellRuntime:
    def __init__(
        self,
        config: HubConfig,
        settings: SkillcellSettings,
        session: aiohttp.ClientSession | None = None,
    ):
        if not settings.enabled:
            raise ValueError("skillcell is disabled")
        self.config = config
        self.settings = settings
        self.store = CellStore(settings.state_db)
        self._source_cache: list[SourceRef] = []
        self._source_errors: dict[str, str] = {}
        self._session = session
        self._clients: list[OpenCodeClient] = []

        nodes = {node.name: node for node in config.nodes}
        executor_name = settings.executor_node or config.nodes[0].name
        if executor_name not in nodes:
            raise ValueError(f"skillcell executor_node not found: {executor_name}")
        self.executor_node: NodeConfig = nodes[executor_name]
        if settings.source_nodes and "*" in settings.source_nodes:
            source_names = [node.name for node in config.nodes]
        else:
            source_names = list(settings.source_nodes) or [executor_name]
        missing = [name for name in source_names if name not in nodes]
        if missing:
            raise ValueError(f"skillcell source_nodes not found: {', '.join(missing)}")
        self.source_nodes: list[NodeConfig] = [nodes[name] for name in source_names]

        self._providers: dict[str, OpenCodeSource] = {}
        self._clients_by_node: dict[str, OpenCodeClient] = {}
        for node in {item.name: item for item in self.source_nodes}.values():
            client = OpenCodeClient(
                node.api_url,
                username=node.username,
                password=node.secret,
                fallback_url=node.web_url,
                timeout=settings.request_timeout_s,
                session=session,
            )
            self._clients.append(client)
            self._clients_by_node[node.name] = client
            self._providers[node.name] = OpenCodeSource(
                client,
                node.name,
                exclude=settings.exclude,
            )

        executor_client = self._clients_by_node.get(executor_name)
        if executor_client is None:
            executor_client = OpenCodeClient(
                self.executor_node.api_url,
                username=self.executor_node.username,
                password=self.executor_node.secret,
                fallback_url=self.executor_node.web_url,
                timeout=settings.request_timeout_s,
                session=session,
            )
            self._clients.append(executor_client)
            self._clients_by_node[executor_name] = executor_client

        self.chat_service = ChatService(
            executor_client,
            self.store,
            directory=settings.executor_directory,
            agent=settings.agent,
            model=settings.model_ref,
            timeout=settings.reasoner_timeout_s,
        )
        self.reasoner = OpenCodeReasoner(
            executor_client,
            directory=settings.executor_directory,
            agent=settings.agent,
            model=settings.model_ref,
            timeout=settings.reasoner_timeout_s,
        )
        self.pipeline = AuditPipeline(
            self.store,
            self.reasoner,
            self._resolve_source,
            PipelineSettings(
                lookback_ms=settings.lookback_ms,
                max_sessions=settings.max_sessions,
                max_session_pages=settings.max_session_pages,
                message_limit=settings.message_limit,
                max_evidence=settings.max_evidence,
                min_score=settings.min_score,
                max_proposals=settings.max_proposals,
            ),
        )
        self.worker = SkillcellWorker(
            self,
            interval_s=settings.interval_s if settings.auto_scan else None,
        )

    async def start(self) -> None:
        await self.refresh_sources()
        await self.worker.start()

    async def stop(self) -> None:
        await self.worker.stop()
        self.store.close()
        for client in self._clients:
            await client.close()

    async def list_sources(self) -> list[SourceRef]:
        if not self._source_cache:
            await self.refresh_sources()
        return list(self._source_cache)

    async def refresh_sources(self) -> list[SourceRef]:
        sources: list[SourceRef] = []
        errors: dict[str, str] = {}
        for name, provider in self._providers.items():
            try:
                sources.extend(await provider.refresh_sources())
            except Exception as exc:
                errors[name] = str(exc)[:200] or exc.__class__.__name__
        executor_directory = self.settings.executor_directory
        if executor_directory:
            sources = [
                item for item in sources if item.directory != executor_directory
            ]
        self._source_cache = sorted(sources, key=lambda item: item.source_id)
        self._source_errors = errors
        return list(self._source_cache)

    def create_job(self, source_id: str, focus: str | None = None) -> Job:
        job = Job(
            id="job_" + uuid.uuid4().hex,
            source_id=source_id,
            status=JobStatus.QUEUED,
            created_at=0,
            updated_at=0,
            focus=(focus.strip()[:4000] if focus and focus.strip() else None),
        )
        return self.store.create_job(job)

    async def request_scan(
        self,
        source_id: str | None = None,
        focus: str | None = None,
    ) -> list[Job]:
        return await self.worker.request_scan(source_id, focus=focus)

    async def status(self) -> dict[str, Any]:
        sources = await self.list_sources()
        proposals = self.store.list_proposals(limit=1000)
        counts = Counter(item.status.value for item in proposals)
        jobs = self.store.list_jobs(limit=20)
        return {
            "enabled": True,
            "executorNode": self.executor_node.name,
            "sourceNodes": [node.name for node in self.source_nodes],
            "autoScan": self.settings.auto_scan,
            "intervalSeconds": self.settings.interval_s,
            "lookbackDays": self.settings.lookback_days,
            "minScore": self.settings.min_score,
            "stateDB": self.settings.state_db,
            "sources": [source_to_dict(item) for item in sources],
            "sourceErrors": self._source_errors,
            "counts": dict(counts),
            "jobs": [job_to_dict(item) for item in jobs],
            "proposalTotal": len(proposals),
        }

    def list_proposals(self, status: ProposalStatus | None = None, limit: int = 200) -> list[dict[str, Any]]:
        return [
            proposal_to_dict(item)
            for item in self.store.list_proposals(status=status, limit=limit)
        ]

    def proposal_detail(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = self.store.get_proposal(proposal_id)
        if proposal is None:
            return None
        evidence = []
        for evidence_id in proposal.evidence_ids:
            item = self.store.get_evidence(evidence_id)
            if item is not None:
                evidence.append(evidence_to_dict(item))
        return {
            **proposal_to_dict(proposal),
            "evidence": evidence,
            "evidenceRefs": [ref.to_dict() for ref in proposal.evidence_refs],
            "sourceRefs": [ref.to_dict() for ref in proposal.source_refs],
            "reviews": [
                {
                    "id": review.id,
                    "decision": review.decision.value,
                    "reviewer": review.reviewer,
                    "rationale": review.rationale,
                    "createdAt": review.created_at,
                }
                for review in self.store.list_reviews(proposal.id)
            ],
        }

    def review(
        self,
        proposal_id: str,
        decision: str,
        *,
        rationale: str = "",
        reviewer: str = "user",
    ) -> dict[str, Any]:
        mapping = {
            "promote": ReviewDecision.PROMOTE,
            "defer": ReviewDecision.DEFER,
            "reject": ReviewDecision.REJECT,
            "reopen": ReviewDecision.REOPEN,
        }
        target = mapping.get(decision)
        if target is None:
            raise ValueError(f"unsupported review decision: {decision}")
        review = Review(
            id="rev_" + uuid.uuid4().hex,
            proposal_id=proposal_id,
            decision=target,
            reviewer=reviewer,
            rationale=rationale,
            created_at=0,
        )
        proposal = self.store.apply_review(review)
        return proposal_to_dict(proposal)

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return [job_to_dict(item) for item in self.store.list_jobs(limit=limit)]

    async def chat_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return await self.chat_service.history(limit=limit)

    async def chat(self, message: str, proposal_id: str | None = None) -> dict[str, Any]:
        context = await self._chat_context(proposal_id)
        session_id, text = await self.chat_service.send(message, context)
        return {"sessionID": session_id, "text": text}

    async def reset_chat(self) -> dict[str, Any]:
        session_id = await self.chat_service.reset()
        return {"sessionID": session_id}

    async def _chat_context(self, proposal_id: str | None) -> str:
        lines: list[str] = []
        if proposal_id:
            proposal = self.store.get_proposal(proposal_id)
            if proposal is None:
                raise KeyError(proposal_id)
            lines.extend(
                [
                    f"Current proposal: {proposal.title}",
                    f"ID: {proposal.id}",
                    f"Type: {proposal.artifact_type.value}",
                    f"Scope: {proposal.scope.value}",
                    f"Status: {proposal.status.value}",
                    f"Score: {proposal.score}",
                    f"Summary: {proposal.summary}",
                    f"Problem: {proposal.problem}",
                    f"Triggers: {', '.join(proposal.triggers) or '(none)'}",
                    f"Exclusions: {', '.join(proposal.exclusions) or '(none)'}",
                    f"Existing skill: {proposal.existing_skill or '(none)'}",
                ]
            )
            used = 0
            for evidence_id in proposal.evidence_ids:
                item = self.store.get_evidence(evidence_id)
                if item is None:
                    continue
                block = f"\n[{item.kind.value}] {item.id}\n{item.excerpt}"
                if used + len(block) > 9000:
                    break
                lines.append(block)
                used += len(block)
            reviews = self.store.list_reviews(proposal.id)
            if reviews:
                lines.append(
                    "Reviews: "
                    + ", ".join(f"{review.decision.value} by {review.reviewer}" for review in reviews)
                )
            lines.append("The user is currently viewing this proposal.")
        else:
            proposals = self.store.list_proposals(limit=10)
            counts = Counter(item.status.value for item in proposals)
            sources = await self.list_sources()
            lines.extend(
                [
                    "No proposal is selected.",
                    f"Known proposals: {len(proposals)}; statuses: {dict(counts)}",
                    f"Sources: {len(sources)}",
                    "Recent proposals:",
                ]
            )
            for item in proposals:
                lines.append(
                    f"- {item.title} ({item.status.value}, score {item.score}, {item.artifact_type.value})"
                )
            lines.append("Ask about a proposal, evidence quality, duplicates, or next steps.")
        text = "\n".join(lines)
        return text[:12000]

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        item = self.store.get_evidence(evidence_id)
        return evidence_to_dict(item) if item is not None else None

    async def _resolve_source(self, source_id: str) -> tuple[OpenCodeSource, SourceRef]:
        for provider in self._providers.values():
            for source in await provider.list_sources():
                if source.source_id == source_id:
                    return provider, source
        await self.refresh_sources()
        for provider in self._providers.values():
            for source in await provider.list_sources():
                if source.source_id == source_id:
                    return provider, source
        raise KeyError(f"unknown skillcell source: {source_id}")
