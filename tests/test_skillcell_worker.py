from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skillcell.models import Job, JobStatus, SourceRef
from skillcell.pipeline import PipelineSettings
from skillcell.store import CellStore
from skillcell.worker import SkillcellWorker


class FakePipeline:
    def __init__(self, store: CellStore) -> None:
        self.store = store
        self.ran: list[str] = []

    async def run_job(self, job: Job) -> Job:
        self.ran.append(job.id)
        self.store.transition_job(job.id, JobStatus.COLLECTING)
        return self.store.transition_job(job.id, JobStatus.COMPLETED)


class FakeHost:
    def __init__(self, store: CellStore) -> None:
        self.store = store
        self.pipeline = FakePipeline(store)
        self.sources = [
            SourceRef(source_id="src-1", node="node-a", directory="/work/a"),
            SourceRef(source_id="src-2", node="node-a", directory="/work/b"),
        ]

    async def list_sources(self):
        return list(self.sources)

    def create_job(self, source_id: str, focus: str | None = None) -> Job:
        return self.store.create_job(
            Job(
                id="job-" + source_id,
                source_id=source_id,
                status=JobStatus.QUEUED,
                created_at=0,
                updated_at=0,
                focus=focus,
            )
        )


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CellStore(Path(self.tmp.name) / "skillcell.db")
        self.host = FakeHost(self.store)
        self.worker = SkillcellWorker(self.host)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def test_request_scan_creates_jobs_and_reuses_active(self):
        jobs = await self.worker.request_scan()
        self.assertEqual([job.source_id for job in jobs], ["src-1", "src-2"])
        again = await self.worker.request_scan("src-1")
        self.assertEqual(again[0].id, jobs[0].id)
        self.assertEqual(len(self.store.list_jobs()), 2)

    async def test_request_scan_stores_focus(self):
        jobs = await self.worker.request_scan("src-1", focus="release hardening")
        self.assertEqual(jobs[0].focus, "release hardening")

    async def test_request_unknown_source(self):
        with self.assertRaises(KeyError):
            await self.worker.request_scan("missing")

    async def test_run_once_processes_queued_jobs(self):
        await self.worker.request_scan()
        processed = await self.worker.run_once()
        self.assertEqual(processed, 2)
        self.assertEqual(sorted(self.host.pipeline.ran), ["job-src-1", "job-src-2"])
        self.assertEqual(
            {job.status for job in self.store.list_jobs()}, {JobStatus.COMPLETED}
        )

    async def test_auto_scan_enqueues_due_sources(self):
        worker = SkillcellWorker(self.host, interval_s=0.001)
        processed = await worker.run_once()
        self.assertEqual(processed, 2)
        cursor = self.store.get_cursor("src-1")
        self.assertIsNone(cursor)

    async def test_start_stop(self):
        await self.worker.start()
        await self.worker.stop()
        self.assertIsNone(self.worker._task)

    async def test_start_recovers_interrupted_job(self):
        job = self.host.create_job("src-1")
        self.store.transition_job(job.id, JobStatus.COLLECTING)
        self.store.transition_job(job.id, JobStatus.ANALYZING)
        await self.worker.start()
        try:
            recovered = self.store.get_job(job.id)
            self.assertEqual(recovered.status, JobStatus.QUEUED)
            self.assertIsNone(recovered.error)
        finally:
            await self.worker.stop()


if __name__ == "__main__":
    unittest.main()
