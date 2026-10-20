"""Shared skill registry with metadata-only discovery and generated manuals."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .knowledge import SCOPES


REGISTRY_SCHEMA_VERSION = 1
SKILL_KINDS = {"portable", "conditional", "provider-specific", "hosted-only"}


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: str
    scope: str
    kind: str
    status: str
    cost_tier: str
    providers: dict[str, str]
    origin: str = "registered"


def _unquote(value: str) -> str:
    """Return a simple YAML scalar without its optional surrounding quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _atomic_json_write(path: Path, value: object) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def parse_skill_metadata(skill_path: Path) -> tuple[str, str]:
    lines = skill_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError(f"Missing front matter: {skill_path}")
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = _unquote(value.strip())
    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError(f"Skill metadata requires name and description: {skill_path}")
    return metadata["name"], metadata["description"]


class SkillRegistry:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path.expanduser().resolve(strict=False)
        if not self.registry_path.parent.is_dir():
            raise ValueError(f"Registry directory does not exist: {self.registry_path.parent}")

    def records(self) -> list[SkillRecord]:
        if not self.registry_path.exists():
            return []
        raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if raw.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise ValueError("Unsupported skill registry schema.")
        items = raw.get("skills", [])
        if not isinstance(items, list):
            raise ValueError("Skill registry skills must be a list.")
        try:
            records = [SkillRecord(**item) for item in items if isinstance(item, dict)]
        except TypeError as error:
            raise ValueError("Skill registry record is invalid.") from error
        if len(records) != len(items):
            raise ValueError("Skill registry record is invalid.")
        return records

    def register(self, source_directory: Path, scope: str, kind: str, cost_tier: str, providers: dict[str, str]) -> SkillRecord:
        if kind not in SKILL_KINDS:
            raise ValueError(f"Unsupported skill kind: {kind}")
        if scope not in SCOPES:
            raise ValueError(f"Unsupported skill scope: {scope}")
        skill_file = source_directory.expanduser().resolve(strict=True) / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError(f"SKILL.md does not exist: {skill_file}")
        name, description = parse_skill_metadata(skill_file)
        record = SkillRecord(name, description, str(skill_file.parent), scope, kind, "approved", cost_tier, providers)
        existing = {item.name: item for item in self.records()}
        if name in existing and existing[name] != record:
            raise ValueError(f"Skill name already registered with different metadata: {name}")
        existing[name] = record
        _atomic_json_write(
            self.registry_path,
            {"schema_version": REGISTRY_SCHEMA_VERSION, "skills": [asdict(item) for item in sorted(existing.values(), key=lambda item: item.name)]},
        )
        return record

    @staticmethod
    def discover(home_directory: Path | None = None) -> list[SkillRecord]:
        """Read installed skill metadata without changing or loading any skill.

        Only stable local installation roots are considered.  Marketplace source
        clones are deliberately excluded: they may describe uninstalled skills.
        A discovered record says ``installed`` for its owning provider; it never
        asserts that another provider can execute the skill.
        """
        home = (home_directory or Path.home()).expanduser().resolve(strict=False)
        roots: list[tuple[Path, str, dict[str, str]]] = [
            (home / "agent-skills", "personal-directory", {}),
            (home / ".codex" / "skills", "codex", {"codex": "installed"}),
            (home / ".claude" / "skills", "claude", {"claude": "installed"}),
            (home / ".claude" / "plugins" / "cache", "claude-plugin", {"claude": "installed"}),
            (home / ".gemini" / "antigravity-cli" / "builtin" / "skills", "antigravity", {"antigravity": "installed"}),
        ]
        discovered: list[SkillRecord] = []
        seen_paths: set[Path] = set()
        for root, origin, providers in roots:
            if not root.is_dir():
                continue
            candidates = root.rglob("SKILL.md")
            for skill_file in sorted(candidates):
                if origin == "claude-plugin" and "skills" not in skill_file.parts:
                    continue
                try:
                    resolved = skill_file.resolve(strict=True)
                    if resolved in seen_paths:
                        continue
                    name, description = parse_skill_metadata(resolved)
                except (OSError, ValueError, UnicodeError):
                    # A malformed third-party skill must not make catalog lookup fail.
                    continue
                seen_paths.add(resolved)
                detail = origin
                if origin == "claude-plugin":
                    try:
                        cache_index = resolved.parts.index("cache")
                        plugin, package, version = resolved.parts[cache_index + 1 : cache_index + 4]
                        detail = f"claude-plugin:{plugin}/{package}@{version}"
                    except (ValueError, IndexError):
                        pass
                discovered.append(
                    SkillRecord(
                        name=name,
                        description=description,
                        path=str(resolved.parent),
                        scope="personal",
                        kind="provider-specific" if providers else "conditional",
                        status="discovered",
                        cost_tier="unknown",
                        providers=dict(providers),
                        origin=detail,
                    )
                )
        return sorted(discovered, key=lambda item: (item.name.casefold(), item.origin, item.path))

    def catalog(self, include_discovered: bool = False, home_directory: Path | None = None) -> list[SkillRecord]:
        """Return registered records plus optional read-only installed discovery."""
        registered = self.records()
        if not include_discovered:
            return registered
        known_paths = {str(Path(item.path).expanduser().resolve(strict=False)) for item in registered}
        merged = list(registered)
        for item in self.discover(home_directory):
            if str(Path(item.path).expanduser().resolve(strict=False)) not in known_paths:
                merged.append(item)
        return sorted(merged, key=lambda item: (item.name.casefold(), item.origin, item.path))

    def search(self, query: str, limit: int = 3, include_discovered: bool = False, home_directory: Path | None = None) -> list[SkillRecord]:
        terms = {term.lower() for term in query.split() if term}
        def score(item: SkillRecord) -> tuple[int, str]:
            text = f"{item.name} {item.description}".lower()
            return (sum(term in text for term in terms), item.name)
        return [item for item in sorted(self.catalog(include_discovered, home_directory), key=score, reverse=True) if score(item)[0]][:limit]

    def write_manual(self, destination: Path, include_discovered: bool = False, home_directory: Path | None = None) -> None:
        rows = ["# 스킬 설명서", "", "발견됨은 해당 제공자의 설치 경로가 확인됐다는 뜻이며, 다른 제공자에서의 실행 가능성을 보장하지 않습니다.", "", "| 이름 | 설명 | 출처 | 제공자 상태 | 범위 | 유형 | 상태 | 비용 |", "|---|---|---|---|---|---|---|---|"]
        for item in self.catalog(include_discovered, home_directory):
            providers = ", ".join(f"{name}={state}" for name, state in sorted(item.providers.items())) or "미확인"
            rows.append(f"| {item.name} | {item.description} | {item.origin} | {providers} | {item.scope} | {item.kind} | {item.status} | {item.cost_tier} |")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite manual: {destination}")
        destination.write_text("\n".join(rows) + "\n", encoding="utf-8")
