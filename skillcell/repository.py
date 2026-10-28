from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path

import yaml

from .clock import now_ms
from .models import (
    ArtifactVersion,
    FileDiff,
    SkillArtifact,
    StagedArtifact,
    ValidationIssue,
    ValidationResult,
)


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
STAGE_RE = re.compile(r"^[a-f0-9]{32}$")
RESERVED_PARTS = {".git", ".skillcell"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
)


def _validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise ValueError(
            "invalid skill name: use 1-64 lowercase letters, digits, '.', '_' or '-'"
        )
    return name


def _safe_relpath(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError(f"invalid path: {value!r}")
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"absolute path is not allowed: {value!r}")
    parts = path.as_posix().split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid path: {value!r}")
    if any(part in RESERVED_PARTS for part in parts):
        raise ValueError(f"reserved path: {value!r}")
    return path.as_posix()


def _read_files(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if not root.exists():
        return files
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(
            name for name in dirnames if not (base / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = base / filename
            if path.is_symlink() or not path.is_file():
                continue
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _hash_files(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(files[name])
        digest.update(b"\x00")
    return digest.hexdigest()


def _parse_frontmatter(text: str) -> dict[str, object]:
    value = text.lstrip("\ufeff")
    lines = value.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        raw = "\n".join(lines[1:index])
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _text_value(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return f"<binary {len(value)} bytes>"


class FilesystemRepository:
    def __init__(self, live_root: str | Path, state_root: str | Path):
        self.live_root = Path(live_root).expanduser().resolve()
        self.state_root = Path(state_root).expanduser().resolve()
        self.staging_root = self.state_root / "staging"
        self.history_root = self.state_root / "history"
        self.live_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.history_root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[SkillArtifact]:
        items: list[SkillArtifact] = []
        for path in sorted(self.live_root.iterdir()):
            if not path.is_dir() or path.is_symlink():
                continue
            artifact = self._artifact_or_none(path.name, path)
            if artifact is not None:
                items.append(artifact)
        return items

    def get(self, name: str) -> SkillArtifact | None:
        _validate_name(name)
        return self._artifact_or_none(name, self.live_root / name)

    def stage(self, name: str, files: Mapping[str, str | bytes]) -> StagedArtifact:
        _validate_name(name)
        if not files:
            raise ValueError("at least one file is required")
        normalized: dict[str, bytes] = {}
        for raw_path, raw_value in files.items():
            rel = _safe_relpath(raw_path)
            if rel in normalized:
                raise ValueError(f"duplicate path: {rel}")
            if isinstance(raw_value, bytes):
                normalized[rel] = raw_value
            else:
                normalized[rel] = raw_value.encode("utf-8")

        staged_id = uuid.uuid4().hex
        stage_dir = self.staging_root / staged_id
        content_dir = stage_dir / "content"
        content_dir.mkdir(parents=True)
        for rel, value in normalized.items():
            target = content_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
        content_hash = _hash_files(normalized)
        meta = {
            "id": staged_id,
            "name": name,
            "files": sorted(normalized),
            "content_hash": content_hash,
            "created_at": now_ms(),
        }
        (stage_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return StagedArtifact(
            id=staged_id,
            name=name,
            path=content_dir,
            files=tuple(sorted(normalized)),
            content_hash=content_hash,
        )

    def validate(self, staged_id: str) -> ValidationResult:
        meta, content_dir = self._load_stage(staged_id)
        errors: list[ValidationIssue] = []
        warnings: list[ValidationIssue] = []
        name = str(meta["name"])
        files = _read_files(content_dir)
        expected_hash = str(meta.get("content_hash") or "")
        if expected_hash and _hash_files(files) != expected_hash:
            errors.append(
                ValidationIssue(path=".", message="staged content hash does not match metadata")
            )
        if not files:
            errors.append(ValidationIssue(path=".", message="staged skill is empty"))
            return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))
        if "SKILL.md" not in files:
            errors.append(ValidationIssue(path="SKILL.md", message="SKILL.md is required"))
        if self._contains_symlink(content_dir):
            errors.append(ValidationIssue(path=".", message="symlinks are not allowed"))
        total_size = sum(len(value) for value in files.values())
        if total_size > 10 * 1024 * 1024:
            errors.append(ValidationIssue(path=".", message="skill exceeds 10 MiB"))
        for rel, value in files.items():
            if len(value) > 1024 * 1024:
                warnings.append(ValidationIssue(path=rel, message="file exceeds 1 MiB"))
            if not value:
                warnings.append(ValidationIssue(path=rel, message="file is empty"))
            text = _text_value(value)
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    warnings.append(
                        ValidationIssue(path=rel, message="possible secret in file")
                    )
                    break
        skill_md = files.get("SKILL.md")
        if skill_md is not None:
            text = skill_md.decode("utf-8", "replace")
            frontmatter = _parse_frontmatter(text)
            frontmatter_name = frontmatter.get("name")
            description = frontmatter.get("description")
            if not frontmatter:
                errors.append(
                    ValidationIssue(path="SKILL.md", message="frontmatter is required")
                )
            if not isinstance(frontmatter_name, str) or not frontmatter_name.strip():
                errors.append(
                    ValidationIssue(path="SKILL.md", message="frontmatter name is required")
                )
            elif frontmatter_name != name:
                errors.append(
                    ValidationIssue(
                        path="SKILL.md",
                        message=f"frontmatter name {frontmatter_name!r} does not match {name!r}",
                    )
                )
            if not isinstance(description, str) or not description.strip():
                errors.append(
                    ValidationIssue(
                        path="SKILL.md", message="frontmatter description is required"
                    )
                )
            if len(text) > 20000:
                warnings.append(
                    ValidationIssue(path="SKILL.md", message="SKILL.md exceeds 20000 characters")
                )
        return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    def publish(self, staged_id: str) -> SkillArtifact:
        meta, content_dir = self._load_stage(staged_id)
        result = self.validate(staged_id)
        if not result.ok:
            messages = "; ".join(f"{issue.path}: {issue.message}" for issue in result.errors)
            raise ValueError(f"staged skill is invalid: {messages}")
        name = str(meta["name"])
        target = self.live_root / name
        if target.exists() and not target.is_dir():
            raise ValueError(f"live path is not a directory: {target}")
        temp = self.live_root.parent / f".{name}.skillcell-new-{uuid.uuid4().hex}"
        old = self.live_root.parent / f".{name}.skillcell-old-{uuid.uuid4().hex}"
        shutil.copytree(content_dir, temp)
        try:
            if target.exists():
                self._backup(name, target)
                os.replace(target, old)
            try:
                os.replace(temp, target)
            except Exception:
                if old.exists() and not target.exists():
                    os.replace(old, target)
                raise
            if old.exists():
                shutil.rmtree(old)
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)
            if old.exists() and target.exists():
                shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(self.staging_root / staged_id, ignore_errors=True)
        artifact = self._artifact_or_none(name, target)
        if artifact is None:
            raise RuntimeError(f"published skill cannot be read: {name}")
        return artifact

    def history(self, name: str) -> list[ArtifactVersion]:
        _validate_name(name)
        root = self.history_root / name
        if not root.exists():
            return []
        items: list[ArtifactVersion] = []
        for path in sorted(root.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            meta_path = path / "meta.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
            files = _read_files(path)
            items.append(
                ArtifactVersion(
                    name=name,
                    version_id=str(meta.get("version_id") or path.name),
                    path=path,
                    content_hash=str(meta.get("content_hash") or _hash_files(files)),
                    created_at=int(meta.get("created_at") or 0),
                )
            )
        return items

    def diff(self, staged_id: str) -> list[FileDiff]:
        meta, content_dir = self._load_stage(staged_id)
        name = str(meta["name"])
        old_files = _read_files(self.live_root / name)
        new_files = _read_files(content_dir)
        diffs: list[FileDiff] = []
        for rel in sorted(set(old_files) | set(new_files)):
            old = old_files.get(rel)
            new = new_files.get(rel)
            if old is None:
                diffs.append(FileDiff(path=rel, change="added", new=_text_value(new or b"")))
            elif new is None:
                diffs.append(FileDiff(path=rel, change="removed", old=_text_value(old)))
            elif old != new:
                diffs.append(
                    FileDiff(
                        path=rel,
                        change="modified",
                        old=_text_value(old),
                        new=_text_value(new),
                    )
                )
        return diffs

    def _artifact_or_none(self, name: str, path: Path) -> SkillArtifact | None:
        if not path.is_dir() or path.is_symlink():
            return None
        files = _read_files(path)
        raw = files.get("SKILL.md")
        if raw is None:
            return None
        text = raw.decode("utf-8", "replace")
        frontmatter = _parse_frontmatter(text)
        description = frontmatter.get("description")
        return SkillArtifact(
            name=name,
            path=path,
            description=description if isinstance(description, str) else "",
            skill_md=text,
            files=tuple(sorted(files)),
            content_hash=_hash_files(files),
            updated_at=int(path.stat().st_mtime * 1000),
        )

    def _load_stage(self, staged_id: str) -> tuple[dict[str, object], Path]:
        if not STAGE_RE.match(staged_id):
            raise ValueError(f"invalid stage id: {staged_id!r}")
        stage_dir = self.staging_root / staged_id
        meta_path = stage_dir / "meta.json"
        content_dir = stage_dir / "content"
        if not meta_path.is_file() or not content_dir.is_dir():
            raise ValueError(f"staged skill not found: {staged_id}")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid stage metadata: {staged_id}") from exc
        if not isinstance(meta, dict) or not isinstance(meta.get("name"), str):
            raise ValueError(f"invalid stage metadata: {staged_id}")
        _validate_name(str(meta["name"]))
        return meta, content_dir

    def _backup(self, name: str, source: Path) -> ArtifactVersion:
        files = _read_files(source)
        content_hash = _hash_files(files)
        created_at = now_ms()
        version_id = f"{created_at}-{content_hash[:12]}"
        target = self.history_root / name / version_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        meta = {
            "version_id": version_id,
            "name": name,
            "content_hash": content_hash,
            "created_at": created_at,
        }
        (target / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return ArtifactVersion(
            name=name,
            version_id=version_id,
            path=target,
            content_hash=content_hash,
            created_at=created_at,
        )

    def _contains_symlink(self, root: Path) -> bool:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            if any((base / name).is_symlink() for name in dirnames):
                return True
            if any((base / name).is_symlink() for name in filenames):
                return True
        return False
