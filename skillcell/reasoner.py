from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import Evidence, SourceRef
from .opencode import OpenCodeClient, OpenCodeError


class ReasonerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReasonerResult:
    proposals: tuple[dict[str, Any], ...]
    session_id: str | None = None
    raw: str = ""


PROMPT_HEADER = """You are skillcell, a careful skill-audit agent.

Your job is to inspect evidence from real OpenCode sessions and decide whether
there is a genuinely reusable capability worth proposing. Do not propose a
skill for trivial commands, one-off project details, secrets, or anything that
is already covered by an existing skill.

Classify the candidate as exactly one of:
- skill: reusable judgment, strategy, or operating guidance an agent executes
- rule: a short constraint that should always apply
- workflow: an ordered process with state transitions
- mcp_tool: a deterministic interface or programmatic operation
- reference: background knowledge, project constraints, or domain facts

Scoring rubric (0-5 each):
- reusability 30
- knowledge value 25
- execution reliability 20
- scope portability 15
- distinctiveness / no duplication 10
Compute score = sum(weight * rating / 5), rounded to one decimal.
Only propose candidates with score >= 60. Prefer improving an existing skill
over creating a duplicate. If evidence is weak, return no proposals.

Every proposal must include clear triggers and non-triggers (exclusions),
a concise problem statement, and evidence IDs from the provided list.

Return only valid JSON. No markdown fences, no commentary.
JSON shape:
{
  "proposals": [
    {
      "title": "short candidate name",
      "artifact_type": "skill|rule|workflow|mcp_tool|reference",
      "scope": "project|personal|shared",
      "score": 0-100,
      "summary": "one-paragraph value statement",
      "problem": "what problem or repeated correction this addresses",
      "triggers": ["when this applies"],
      "exclusions": ["when this must not apply"],
      "existing_skill": "name or null",
      "evidence_ids": ["ev_..."],
      "rationale": "why the evidence supports this"
    }
  ]
}
"""


def _response_text(response: Mapping[str, Any]) -> str:
    parts = response.get("parts") if isinstance(response.get("parts"), list) else []
    texts = [
        str(part.get("text"))
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and not part.get("ignored")
        and part.get("text")
    ]
    return "\n\n".join(texts).strip()


def _json_candidates(text: str) -> list[str]:
    values: list[str] = []
    for match in re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        values.append(match.strip())
    values.append(text.strip())
    return values


def _parse_proposals(text: str) -> list[dict[str, Any]]:
    for candidate in _json_candidates(text):
        start_candidates = [index for index in (candidate.find("["), candidate.find("{")) if index >= 0]
        if not start_candidates:
            continue
        start = min(start_candidates)
        end = max(candidate.rfind("]"), candidate.rfind("}"))
        if end <= start:
            continue
        raw = candidate[start : end + 1]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            raw_items = value.get("proposals")
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = None
        if not isinstance(raw_items, list):
            continue
        return [item for item in raw_items if isinstance(item, dict)]
    raise ReasonerError("reasoner did not return valid proposal JSON")


class OpenCodeReasoner:
    def __init__(
        self,
        client: OpenCodeClient,
        *,
        directory: str | None = None,
        agent: str | None = None,
        model: Mapping[str, Any] | None = None,
        timeout: float = 300.0,
        max_evidence_chars: int = 30000,
        max_skills: int = 50,
    ):
        self.client = client
        self.directory = directory
        self.agent = agent
        self.model = dict(model) if model else None
        self.timeout = timeout
        self.max_evidence_chars = max(2000, max_evidence_chars)
        self.max_skills = max(1, max_skills)

    async def propose(
        self,
        evidence: Sequence[Evidence],
        existing_skills: Sequence[Mapping[str, Any]],
        source: SourceRef,
        *,
        focus: str | None = None,
    ) -> ReasonerResult:
        prompt = self._build_prompt(evidence, existing_skills, source, focus=focus)
        directory = self.directory or source.directory
        session = await self.client.create_session(
            directory=directory,
            title=f"skillcell: {source.source_id}",
            agent=self.agent,
            model=self.model,
        )
        session_id = session.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ReasonerError("reasoner session creation failed")
        try:
            response = await self.client.prompt(
                session_id,
                prompt,
                directory=directory,
                agent=self.agent,
                model=self.model,
                tools={
                    "edit": False,
                    "write": False,
                    "patch": False,
                    "bash": False,
                    "task": False,
                },
                timeout=self.timeout,
            )
        except OpenCodeError as exc:
            raise ReasonerError(f"reasoner prompt failed: {exc}") from exc
        text = _response_text(response)
        if not text:
            raise ReasonerError("reasoner returned an empty response")
        proposals = _parse_proposals(text)
        return ReasonerResult(proposals=tuple(proposals), session_id=session_id, raw=text)

    def _build_prompt(
        self,
        evidence: Sequence[Evidence],
        existing_skills: Sequence[Mapping[str, Any]],
        source: SourceRef,
        *,
        focus: str | None = None,
    ) -> str:
        skills = []
        for skill in existing_skills[: self.max_skills]:
            name = str(skill.get("name") or "").strip()
            description = str(skill.get("description") or "").strip()
            if name:
                skills.append(f"- {name}: {description[:300]}")
        skill_text = "\n".join(skills) if skills else "(none)"
        blocks: list[str] = []
        used = 0
        for item in evidence:
            block = (
                f"### {item.id}\n"
                f"kind: {item.kind.value}\n"
                f"source: {item.ref.source_id}\n"
                f"session: {item.ref.session_id}\n"
                f"message: {item.ref.message_id or '-'}\n"
                f"part: {item.ref.part_id or '-'}\n"
                f"excerpt:\n{item.excerpt}\n"
            )
            if used + len(block) > self.max_evidence_chars:
                break
            blocks.append(block)
            used += len(block)
        evidence_text = "\n".join(blocks) if blocks else "(none)"
        focus_text = (
            f"\nUser focus:\n{focus.strip()[:4000]}\n"
            "Prioritize a candidate that matches this focus when the evidence supports it.\n"
            "Still apply the hard gates; if evidence is insufficient, return no proposal.\n"
            if focus and focus.strip()
            else ""
        )
        return (
            f"{PROMPT_HEADER}\n"
            f"{focus_text}"
            f"Source node: {source.node or '-'}\n"
            f"Source project: {source.project_id or '-'}\n"
            f"Source directory: {source.directory or '-'}\n\n"
            f"Existing skills:\n{skill_text}\n\n"
            f"Evidence:\n{evidence_text}\n"
        )
