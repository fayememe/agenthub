from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .models import (
    ArtifactVersion,
    Evidence,
    EvidenceRef,
    FileDiff,
    ReasonerRequest,
    SessionPage,
    SkillArtifact,
    SourceRef,
    StagedArtifact,
    ValidationResult,
)


class SkillRepository(Protocol):
    def list(self) -> list[SkillArtifact]: ...

    def get(self, name: str) -> SkillArtifact | None: ...

    def stage(self, name: str, files: Mapping[str, str | bytes]) -> StagedArtifact: ...

    def validate(self, staged_id: str) -> ValidationResult: ...

    def publish(self, staged_id: str) -> SkillArtifact: ...

    def history(self, name: str) -> list[ArtifactVersion]: ...

    def diff(self, staged_id: str) -> list[FileDiff]: ...


class SourceProvider(Protocol):
    async def list_sources(self) -> Sequence[SourceRef]: ...

    async def list_sessions(
        self,
        source: SourceRef,
        cursor: str | None = None,
        limit: int = 100,
    ) -> SessionPage: ...

    async def read_evidence(self, ref: EvidenceRef) -> Evidence: ...


class Reasoner(Protocol):
    async def run(self, request: ReasonerRequest) -> dict[str, Any]: ...
