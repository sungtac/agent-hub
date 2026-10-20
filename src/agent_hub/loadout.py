"""Central, provider-neutral Loadout catalogue and safe enablement state.

This module deliberately compiles *descriptions* into per-provider overlays.  It
never rewrites a provider's global configuration, so a bad overlay can be
removed without affecting Codex, Claude, or another installed client.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


LOADOUT_SCHEMA_VERSION = 1
SUPPORTED_PROVIDERS = {"codex", "claude", "antigravity"}


@dataclass(frozen=True)
class LoadoutModule:
    name: str
    description: str
    enabled: bool
    providers: tuple[str, ...]


DEFAULT_MODULES = (
    LoadoutModule("skill-registry", "공용 스킬 메타데이터 검색 및 설명서", True, ("codex", "claude", "antigravity")),
    LoadoutModule("knowledge-mesh", "범위 분리 지식그물 저장소", True, ("codex", "claude", "antigravity")),
    LoadoutModule("orchestration-handoff", "공통 원장 기반 오케스트레이터 전환 지침", True, ("codex", "claude", "antigravity")),
    LoadoutModule("improvement-observer", "제한된 자가개선 후보 관찰", True, ("codex", "claude", "antigravity")),
)


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


def _atomic_text_write(path: Path, value: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class LoadoutCatalog:
    """Persistent central catalogue; only named modules can be toggled."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path.expanduser().resolve(strict=False)
        if not self.state_path.parent.is_dir():
            raise ValueError(f"Loadout directory does not exist: {self.state_path.parent}")

    def initialise(self) -> list[LoadoutModule]:
        if not self.state_path.exists():
            _atomic_json_write(self.state_path, self._serialise(DEFAULT_MODULES))
        return self.modules()

    def modules(self) -> list[LoadoutModule]:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read Loadout state: {self.state_path}") from error
        if raw.get("schema_version") != LOADOUT_SCHEMA_VERSION:
            raise ValueError("Unsupported Loadout schema.")
        modules = [LoadoutModule(item["name"], item["description"], item["enabled"], tuple(item["providers"])) for item in raw.get("modules", [])]
        expected = {module.name for module in DEFAULT_MODULES}
        actual = {module.name for module in modules}
        if actual != expected or any(not set(module.providers) <= SUPPORTED_PROVIDERS for module in modules):
            raise ValueError("Invalid Loadout module set.")
        return sorted(modules, key=lambda module: module.name)

    def unified_catalog(self, plugin_root: Path, project_root: Path | None = None) -> dict[str, object]:
        """Read catalog entries separately from project installation evidence."""
        plugin = plugin_root.expanduser().resolve(strict=False)
        fragments = plugin / "skills/configure-store/generator/fragments"
        if not fragments.is_dir():
            raise ValueError(f"Loadout plugin fragments not found: {fragments}")
        installed: set[str] = set()
        project: Path | None = None
        if project_root is not None:
            project = project_root.expanduser().resolve(strict=True)
            if not project.is_dir():
                raise ValueError("Project root must be a directory.")
            for name in ("AGENTS.md", "CLAUDE.md"):
                path = project / name
                if path.is_file():
                    installed.update(re.findall(r"<!-- store:([a-z0-9-]+):start -->", path.read_text(encoding="utf-8")))
        items = []
        for path in sorted(fragments.glob("*/meta.json")):
            if path.parent.name.startswith("_"):
                continue
            meta = json.loads(path.read_text(encoding="utf-8"))
            marker_state = "not_checked" if project is None else ("present" if path.parent.name in installed else "absent")
            scaffold_detected = bool(
                project is not None
                and meta.get("scaffold")
                and (project / "_shared" / "orchestrator-rules.md").is_file()
            )
            if project is None:
                installation_evidence = "not_checked"
                project_installation = "not_checked"
            elif marker_state == "present":
                installation_evidence = "instruction_marker"
                project_installation = "detected"
            elif scaffold_detected:
                # The official multi-agent Store installer delegates to a
                # scaffold and deliberately does not leave a store:* block.
                installation_evidence = "scaffold_artifact"
                project_installation = "detected"
            else:
                installation_evidence = "none"
                project_installation = "not_detected"
            items.append({
                "name": path.parent.name,
                "label": meta["label"],
                "category": meta["corner"],
                "description": meta["desc"],
                # This comes from the official plugin catalogue only.  It is
                # deliberately not called "available": that word is easily
                # mistaken for installation or runtime activation.
                "catalog_listed": bool(meta["available"]),
                "flavors": meta.get("flavors"),
                # This is specifically a Loadout Store instruction marker,
                # never an Agent Hub marker and never runtime proof.
                "managed_marker": marker_state,
                # A scaffold is a distinct official installation mechanism;
                # report it separately so an absent marker is not misread as
                # a missing multi-agent installation.
                "project_installation": project_installation,
                "installation_evidence": installation_evidence,
            })
        return {"hub_modules": [asdict(module) for module in self.modules()], "loadout_fragments": items, "plugin_root": str(plugin), "project_root": str(project) if project is not None else None}

    def set_enabled(self, name: str, enabled: bool) -> LoadoutModule:
        modules = self.modules()
        replacement: LoadoutModule | None = None
        updated: list[LoadoutModule] = []
        for module in modules:
            if module.name == name:
                replacement = LoadoutModule(module.name, module.description, enabled, module.providers)
                updated.append(replacement)
            else:
                updated.append(module)
        if replacement is None:
            raise ValueError(f"Unknown Loadout module: {name}")
        _atomic_json_write(self.state_path, self._serialise(updated))
        return replacement

    def enabled_for(self, provider: str) -> list[LoadoutModule]:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        return [module for module in self.modules() if module.enabled and provider in module.providers]

    def write_new_overlay(self, provider: str, project_root: Path, destination: Path, replace_managed: bool = False) -> None:
        """Write or explicitly refresh a Hub-managed project overlay only."""
        if provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}")
        project = project_root.expanduser().resolve(strict=True)
        output = destination.expanduser().resolve(strict=False)
        try:
            output.relative_to(project)
        except ValueError as error:
            raise ValueError("Overlay must be located inside the project root.") from error
        if output.exists():
            if not replace_managed:
                raise FileExistsError(f"Refusing to overwrite overlay: {output}")
            if output.is_symlink() or not output.is_file():
                raise ValueError("Managed overlay refresh requires a regular file.")
            existing = output.read_text(encoding="utf-8")
            if not existing.startswith("# Agent Hub Loadout overlay\n") or "managed_by: agent-hub\n" not in existing or "scope: project-only\n" not in existing:
                raise ValueError("Refusing to replace a file not managed by Agent Hub.")
        # This is an Agent Hub-owned project artefact, never a provider-global
        # configuration file. A contained parent can therefore be created on
        # first use without requiring a manual setup step.
        output.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text_write(output, self._render_overlay(provider))

    def verify_overlays(self, project_root: Path) -> dict[str, str]:
        """Report whether the three standard project overlays match Loadout state."""

        project = project_root.expanduser().resolve(strict=True)
        result: dict[str, str] = {}
        for provider in sorted(SUPPORTED_PROVIDERS):
            output = project / ".agent-hub" / f"loadout.{provider}.md"
            if not output.exists():
                result[provider] = "missing"
            elif output.is_symlink() or not output.is_file():
                result[provider] = "unsafe"
            else:
                content = output.read_text(encoding="utf-8")
                if content == self._render_overlay(provider):
                    result[provider] = "current"
                elif content.startswith("# Agent Hub Loadout overlay\n") and "managed_by: agent-hub\n" in content and "scope: project-only\n" in content:
                    result[provider] = "stale"
                else:
                    result[provider] = "unmanaged"
        return result

    def _render_overlay(self, provider: str) -> str:
        lines = [
            "# Agent Hub Loadout overlay",
            "",
            f"provider: {provider}",
            "managed_by: agent-hub",
            "scope: project-only",
            "",
            "# 활성 모듈",
        ]
        lines.extend(f"- {module.name}: {module.description}" for module in self.enabled_for(provider))
        if any(module.name == "orchestration-handoff" for module in self.enabled_for(provider)):
            lines.extend((
                "",
                "# 공통 오케스트레이션 계약",
                "- 작업 전환은 provider 내부 대화를 복사하지 말고 동일 task ID의 Agent Hub 원장을 정본으로 사용합니다.",
                "- 사용자가 전환을 명시하면 `agent-hub natural --text 'TARGET을 오케스트레이터로 전환해줘' --apply --task-id TASK --provider TARGET --instruction 'CONTINUE_FROM_LEDGER'`를 사용합니다.",
                "- 살아 있는 lease를 강탈하지 말고, 사용량 제한·timeout·network 불가만 Hub router의 동일 task handoff에 맡깁니다.",
            ))
        lines.extend((
            "",
            "# Architecture evidence gate",
            "- 기능의 부재·미완성·완성 또는 신규 설계를 주장하기 전에 CLI 진입점, durable state/task ledger, 승인 경계, 실행·rollback 경로, 관련 테스트를 순서대로 확인하고 근거를 제시합니다.",
            "- 하나라도 확인하지 못하면 `미확인`으로 보고하며, 일부 모듈만 보고 아키텍처 결론을 내리지 않습니다.",
            "- 새 구성요소를 제안하기 전에 기존 실행 경로가 같은 동작을 이미 제공하는지 확인합니다.",
        ))
        lines.extend(("", "# 이 파일은 공급자 전역 설정을 변경하지 않습니다.", "# 변경하려면 새 파일을 생성하거나 중앙 Loadout 상태를 갱신하십시오."))
        return "\n".join(lines) + "\n"

    def write_manual(self, destination: Path) -> None:
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite manual: {destination}")
        if not destination.parent.is_dir():
            raise ValueError(f"Manual directory does not exist: {destination.parent}")
        lines = ["# Loadout 설명서", "", "| 모듈 | 설명 | 상태 | 적용 대상 |", "|---|---|---|---|"]
        for module in self.modules():
            state = "켜짐" if module.enabled else "꺼짐"
            lines.append(f"| {module.name} | {module.description} | {state} | {', '.join(module.providers)} |")
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _serialise(modules: tuple[LoadoutModule, ...] | list[LoadoutModule]) -> dict[str, object]:
        return {"schema_version": LOADOUT_SCHEMA_VERSION, "modules": [asdict(module) for module in sorted(modules, key=lambda item: item.name)]}
