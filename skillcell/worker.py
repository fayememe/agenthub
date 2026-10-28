from __future__ import annotations

import asyncio
from typing import Protocol

from .clock import now_ms
from .models import Job, JobStatus, SourceRef
from .pipeline import AuditPipeline
from .store import CellStore


class WorkerHost(Protocol):
    store: CellStore
    pipeline: AuditPipeline

    async def list_sources(self) -> list[SourceRef]: ...

    def create_job(self, source_id: str, focus: str | None = None) -> Job: ...


class SkillcellWorker:
    def __init__(
        self,
        host: WorkerHost,
        *,
        interval_s: float | None = None,
        poll_interval_s: float = 30.0,
    ):
        self.host = host
        self.interval_s = interval_s
        self.poll_interval_s = max(1.0, poll_interval_s)
        self._wake = asyncio.Event()
        self._stop = False
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._processing = asyncio.Lock()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._recover_interrupted()
        self._stop = False
        self._task = asyncio.create_task(self._loop(), name="skillcell-worker")

    def _recover_interrupted(self) -> None:
        for job in self.host.store.list_jobs(limit=1000):
            if job.status not in {
                JobStatus.COLLECTING,
                JobStatus.ANALYZING,
                JobStatus.PARTIAL,
            }:
                continue
            try:
                self.host.store.transition_job(
                    job.id, JobStatus.FAILED, error="interrupted by worker restart"
                )
                self.host.store.transition_job(job.id, JobStatus.QUEUED)
            except Exception:
                continue

    async def stop(self) -> None:
        self._stop = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def wake(self) -> None:
        self._wake.set()

    async def request_scan(
        self,
        source_id: str | None = None,
        focus: str | None = None,
    ) -> list[Job]:
        async with self._lock:
            sources = await self.host.list_sources()
            if source_id is not None:
                sources = [item for item in sources if item.source_id == source_id]
                if not sources:
                    raise KeyError(source_id)
            jobs: list[Job] = []
            for source in sources:
                active = self.host.store.active_job(source.source_id)
                if active is not None:
                    jobs.append(active)
                    continue
                jobs.append(self.host.create_job(source.source_id, focus=focus))
        self.wake()
        return jobs

    async def run_once(self) -> int:
        if self._processing.locked():
            return 0
        async with self._processing:
            if self.interval_s is not None:
                await self._enqueue_due()
            queued = self.host.store.list_jobs(status=JobStatus.QUEUED, limit=10)
            processed = 0
            for job in queued:
                if self._stop:
                    break
                try:
                    await self.host.pipeline.run_job(job)
                except Exception:
                    pass
                processed += 1
            return processed

    async def _enqueue_due(self) -> None:
        if not self.interval_s or self.interval_s <= 0:
            return
        interval_ms = int(self.interval_s * 1000)
        at = now_ms()
        async with self._lock:
            for source in await self.host.list_sources():
                if self.host.store.active_job(source.source_id) is not None:
                    continue
                cursor = self.host.store.get_cursor(source.source_id)
                if cursor is None or at - cursor.updated_at >= interval_ms:
                    self.host.create_job(source.source_id)

    async def _loop(self) -> None:
        while not self._stop:
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                return
            except Exception:
                processed = 0
            if processed:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
