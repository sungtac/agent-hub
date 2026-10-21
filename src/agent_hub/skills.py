"""Shared skill registry with metadata-only discovery and generated manuals."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
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


_BLOCK_SCALAR = re.compile(r"^([|>])([+-]?)([1-9]?)([+-]?)(?:\s+#.*)?$")


def _block_scalar(style: str, chomping: str, explicit_indent: int, body: list[str]) -> str:
    """Return a YAML literal (``|``) or folded (``>``) block scalar value."""
    indent = explicit_indent
    if not indent:
        indent = next((len(line) - len(line.lstrip(" ")) for line in body if line.strip()), 0)
    lines = [line[indent:] if line.strip() else "" for line in body]
    trailing = 0
    while lines and lines[-1] == "":
        lines.pop()
        trailing += 1
    if style == "|":
        text = "\n".join(lines)
    else:
        # Folding: a break between two normal (not more-indented) non-empty
        # lines becomes a space, each empty line becomes a newline, and breaks
        # around more-indented lines are kept.
        def normal(item: str) -> bool:
            return bool(item) and not item.startswith((" ", "\t"))

        text = lines[0] if lines else ""
        last_content = lines[0] if lines else ""
        for previous, line in zip(lines, lines[1:]):
            if line == "":
                text += "\n"
                continue
            if normal(last_content) and normal(line):
                text += line if previous == "" else " " + line
            else:
                text += "\n" + line
            last_content = line
    if chomping == "+":
        # Keep: the final line break plus every trailing empty line.
        return text + "\n" * (trailing + (1 if lines else 0))
    if chomping == "-" or not lines:
        return text
    # Clip (default): exactly one final line break.
    return text + "\n"


def parse_front_matter(lines: list[str]) -> dict[str, str]:
    """Parse top-level scalar keys of a SKILL.md front matter block.

    Supports plain, quoted, multi-line plain, and folded/literal block scalars
    (``>``/``|`` with optional chomping and indentation indicators).  Nested
    mappings are skipped.  Standard library only.
    """
    metadata: dict[str, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line == "---":
            break
        if not line.strip() or line.lstrip().startswith("#") or line[0] in " \t":
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        value = value.strip()
        body: list[str] = []
        while index < len(lines) and lines[index] != "---" and (not lines[index].strip() or lines[index][0] in " \t"):
            body.append(lines[index])
            index += 1
        block = _BLOCK_SCALAR.match(value)
        if block:
            style, first, explicit, second = block.groups()
            metadata[key.strip()] = _block_scalar(style, first or second, int(explicit or 0), body)
        elif value:
            continuation = [item.strip() for item in body if item.strip()]
            metadata[key.strip()] = _unquote(" ".join([value, *continuation]))
        # An empty value introduces a nested mapping or sequence; skip it.
    return metadata


def parse_skill_metadata(skill_path: Path) -> tuple[str, str]:
    lines = skill_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[0] != "---":
        raise ValueError(f"Missing front matter: {skill_path}")
    metadata = {key: value.strip() for key, value in parse_front_matter(lines[1:]).items()}
    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError(f"Skill metadata requires name and description: {skill_path}")
    return metadata["name"], metadata["description"]


def _skill_files(root: Path) -> list[Path]:
    """Return every ``SKILL.md`` below ``root``, following directory symlinks.

    ``Path.rglob`` does not descend into symlinked directories, which hid every
    skill installed as a symlink.  Directories are walked once per resolved
    target so a symlink cycle cannot recurse forever.
    """
    files: list[Path] = []
    visited: set[str] = set()
    for directory, subdirectories, filenames in os.walk(root, followlinks=True):
        real = os.path.realpath(directory)
        if real in visited:
            subdirectories[:] = []
            continue
        visited.add(real)
        subdirectories.sort()
        if "SKILL.md" in filenames:
            files.append(Path(directory) / "SKILL.md")
    return files


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
        Provider skill directories are followed through symlinks, so a shared
        skill linked into both ``~/.codex/skills`` and ``~/.claude/skills`` is
        reported once, as ``installed`` for every provider whose directory has
        it.  Codex and Claude plugin caches are scanned symmetrically.  A record
        never asserts that a provider lacking the installation can run it.
        """
        home = (home_directory or Path.home()).expanduser().resolve(strict=False)
        roots: list[tuple[Path, str, str | None]] = [
            (home / "agent-skills", "personal-directory", None),
            (home / ".codex" / "skills", "codex", "codex"),
            (home / ".claude" / "skills", "claude", "claude"),
            (home / ".codex" / "plugins" / "cache", "codex-plugin", "codex"),
            (home / ".claude" / "plugins" / "cache", "claude-plugin", "claude"),
            (home / ".gemini" / "antigravity-cli" / "builtin" / "skills", "antigravity", "antigravity"),
        ]
        # One entry per resolved skill file: metadata plus every provider and
        # origin that installs that same target.
        found: dict[Path, tuple[str, str, dict[str, str], list[str]]] = {}
        for root, origin, provider in roots:
            if not root.is_dir():
                continue
            for skill_file in _skill_files(root):
                plugin = origin.endswith("-plugin")
                if plugin and "skills" not in skill_file.relative_to(root).parts:
                    continue
                try:
                    resolved = skill_file.resolve(strict=True)
                    if resolved not in found:
                        name, description = parse_skill_metadata(resolved)
                        found[resolved] = (name, description, {}, [])
                except (OSError, ValueError, UnicodeError):
                    # A malformed third-party skill must not make catalog lookup fail.
                    continue
                detail = origin
                if plugin:
                    parts = skill_file.relative_to(root).parts
                    if len(parts) >= 4:
                        detail = f"{origin}:{parts[0]}/{parts[1]}@{parts[2]}"
                _, _, providers, origins = found[resolved]
                if provider:
                    providers[provider] = "installed"
                if detail not in origins:
                    origins.append(detail)
        discovered = [
            SkillRecord(
                name=name,
                description=description,
                path=str(resolved.parent),
                scope="personal",
                kind="provider-specific" if len(providers) == 1 else "conditional",
                status="discovered",
                cost_tier="unknown",
                providers=dict(sorted(providers.items())),
                origin=",".join(origins),
            )
            for resolved, (name, description, providers, origins) in found.items()
        ]
        return sorted(discovered, key=lambda item: (item.name.casefold(), item.origin, item.path))

    def catalog(self, include_discovered: bool = False, home_directory: Path | None = None) -> list[SkillRecord]:
        """Return registered records plus optional read-only installed discovery.

        A discovered installation of a registered path is not listed twice; it
        only adds ``installed`` for providers the registration leaves unstated.
        """
        registered = self.records()
        if not include_discovered:
            return registered
        installed = {str(Path(item.path).expanduser().resolve(strict=False)): item for item in self.discover(home_directory)}
        merged: list[SkillRecord] = []
        for item in registered:
            found = installed.pop(str(Path(item.path).expanduser().resolve(strict=False)), None)
            if found is not None:
                item = replace(item, providers={**found.providers, **item.providers})
            merged.append(item)
        merged.extend(installed.values())
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
