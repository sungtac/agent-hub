"""Unified management over Hub modules and the official Loadout installer."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from .loadout import DEFAULT_MODULES, LoadoutCatalog


def installed_plugin_root() -> Path:
    registry = Path.home() / ".claude/plugins/installed_plugins.json"
    entries = json.loads(registry.read_text(encoding="utf-8")).get("plugins", {}).get("loadout@loadout", [])
    user_entries = [entry for entry in entries if entry.get("scope") == "user"]
    if len(user_entries) != 1 or not isinstance(user_entries[0].get("installPath"), str):
        raise ValueError("Select one Loadout installation with --plugin-root.")
    return Path(user_entries[0]["installPath"]).expanduser().resolve(strict=True)


def _run(command: list[str], timeout: float = 90) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Command timed out after {timeout:g}s; inspect the target before retrying.") from error


def _check_path(project: Path, path: Path) -> None:
    """Reject links anywhere below the selected project before touching a file."""
    relative = path.relative_to(project)
    cursor = project
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Refusing symlink in managed path: {cursor}")


def _fragment_paths(plugin: Path, project: Path, name: str, flavor: str, installing: bool) -> set[Path]:
    fragment = plugin / "skills/configure-store/generator/fragments" / name
    instruction = project / ("CLAUDE.md" if flavor == "claude" else "AGENTS.md")
    touched = {instruction}
    _check_path(project, instruction)
    assets: dict[Path, Path] = {}
    for folder in [fragment / "files"] + ([fragment / "files.codex"] if flavor == "codex" else []):
        if not folder.is_dir():
            continue
        for source in sorted(folder.rglob("*")):
            if source.is_symlink():
                raise ValueError(f"Refusing symlink in plugin assets: {source}")
            if not source.is_file():
                continue
            destination = project / source.relative_to(folder)
            _check_path(project, destination)
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Managed asset is not a regular file: {destination}")
            # The installer copies the common tree first, then the flavor tree.
            assets[destination] = source
    for destination, source in assets.items():
        if installing and destination.is_file() and destination.read_bytes() != source.read_bytes():
            raise ValueError(f"Refusing to overwrite an existing modified asset: {destination}")
        touched.add(destination)
    meta_path = fragment / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    if meta.get("claude_settings_hook") and flavor == "claude":
        settings = project / ".claude/settings.json"
        _check_path(project, settings)
        if settings.exists():
            if not settings.is_file():
                raise ValueError("Claude project settings must be a regular file.")
            data = json.loads(settings.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
                raise ValueError("Refusing to replace malformed Claude hook settings.")
            hooks = data.get("hooks", {})
            if not isinstance(hooks.get("Stop", []), list):
                raise ValueError("Refusing to replace malformed Claude Stop hooks.")
            matching = [entry for entry in hooks.get("Stop", []) if "coach --hook" in json.dumps(entry, ensure_ascii=False)]
            if matching:
                canonical = json.loads((fragment / meta["claude_settings_hook"]).read_text(encoding="utf-8"))
                if any(entry != canonical for entry in matching):
                    raise ValueError("Refusing to replace or remove a modified coach Stop hook.")
        touched.add(settings)
    return touched


def _marker_blocks(text: str) -> dict[str, str]:
    """Reject duplicate, unbalanced, crossed or nested managed blocks."""
    blocks: dict[str, str] = {}
    opened: tuple[str, int] | None = None
    for match in re.finditer(r"<!-- store:([a-z0-9-]+):(start|end) -->", text):
        name, kind = match.groups()
        if kind == "start":
            if opened is not None or name in blocks:
                raise ValueError(f"Refusing to alter malformed, nested or duplicate Loadout markers: {name}")
            opened = (name, match.start())
        else:
            if opened is None or opened[0] != name:
                raise ValueError(f"Refusing to alter malformed, nested or duplicate Loadout markers: {name}")
            blocks[name] = text[opened[1]:match.end()]
            opened = None
    if opened is not None:
        raise ValueError(f"Refusing to alter malformed, nested or duplicate Loadout markers: {opened[0]}")
    return blocks


def _snapshot(paths: set[Path]) -> dict[Path, tuple[bytes, int] | None]:
    saved: dict[Path, tuple[bytes, int] | None] = {}
    for path in paths:
        if path.exists():
            if not path.is_file():
                raise ValueError(f"Managed path is not a regular file: {path}")
            saved[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        else:
            saved[path] = None
    return saved


def _restore(project: Path, saved: dict[Path, tuple[bytes, int] | None]) -> None:
    for path, previous in saved.items():
        _check_path(project, path)
        if previous is None:
            if path.exists():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(previous[0])
            path.chmod(previous[1])


def manage_hub(catalog: LoadoutCatalog, name: str, action: str, apply: bool) -> dict[str, Any]:
    using_default = not catalog.state_path.exists()
    modules = DEFAULT_MODULES if using_default else catalog.modules()
    module = next((item for item in modules if item.name == name), None)
    if module is None:
        raise ValueError(f"Unknown Hub module: {name}")
    if action == "status":
        return {"source": "hub", "name": name, "configured_enabled": module.enabled, "using_default": using_default, "applied": False}
    if action not in {"enable", "disable"}:
        raise ValueError("Built-in Hub modules support status, enable and disable; they are not installable or removable.")
    enabled = action == "enable"
    if apply:
        catalog.initialise()
        catalog.set_enabled(name, enabled)
    return {"source": "hub", "name": name, "previous_enabled": module.enabled, "using_default": using_default, "requested_enabled": enabled, "applied": apply, "note": "Existing project overlays may need an explicit refresh; runtime activation is not inferred."}


def manage_guard(action: str, apply: bool) -> dict[str, Any]:
    if action not in {"status", "enable", "disable"}:
        raise ValueError("The guard runtime supports status, enable and disable.")
    coach = shutil.which("coach")
    if coach is None:
        raise ValueError("coach is not available on PATH.")
    if action != "status" and not apply:
        return {"source": "guard-runtime", "action": action, "applied": False, "note": "Preview only; the global coach guard switch was not changed."}
    if action != "status":
        change = _run([coach, "guard", "on" if action == "enable" else "off"], timeout=15)
        if change.returncode:
            raise RuntimeError(f"coach guard failed (exit {change.returncode}): {change.stderr or change.stdout}")
    # Suppress slow live usage lookups; this probes the switch, not quota safety.
    status = _run([coach, "guard", "status", "--providers", ""], timeout=15)
    match = re.search(r"^요금가드:\s*(on|off)\s*$", status.stdout, re.MULTILINE)
    if status.returncode or not match:
        raise RuntimeError("Could not verify the coach guard switch; it may have changed. Inspect coach guard status.")
    enabled = match.group(1) == "on"
    if action != "status" and enabled != (action == "enable"):
        raise RuntimeError("coach reported success, but the guard switch did not reach the requested state.")
    return {"source": "guard-runtime", "action": action, "applied": action != "status", "runtime_enabled": enabled, "usage_check_performed": False}


def manage_fragment(plugin: Path, project: Path, name: str | None, action: str, flavor: str | None, apply: bool, replace_modified_block: bool = False) -> dict[str, Any]:
    if replace_modified_block and action not in {"install", "enable"}:
        raise ValueError("--replace-modified-block only applies to fragment install or enable.")
    project = project.expanduser().resolve(strict=True)
    plugin = plugin.expanduser().resolve(strict=True)
    if not project.is_dir() or not plugin.is_dir():
        raise ValueError("Project and plugin roots must be directories.")
    script = plugin / "skills/configure-store/generator/store.py"
    if not script.is_file():
        raise ValueError(f"Official Loadout installer not found: {script}")
    if action == "doctor":
        result = _run([sys.executable, str(script), "--target", str(project), "--doctor"])
        if result.returncode:
            raise ValueError(f"Loadout doctor reported a failure (exit {result.returncode}):\n{result.stdout}{result.stderr}")
        return {"source": "fragment", "action": "doctor", "scope": "project", "project_root": str(project), "applied": False, "output": result.stdout.strip()}
    if name is None or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None:
        raise ValueError("Fragment management requires a valid Loadout fragment name.")
    if flavor not in {"claude", "codex"}:
        raise ValueError("Loadout fragment flavor must be claude or codex.")
    instruction = project / ("CLAUDE.md" if flavor == "claude" else "AGENTS.md")
    _check_path(project, instruction)
    if action == "status":
        text = instruction.read_text(encoding="utf-8") if instruction.is_file() else ""
        start, end = f"<!-- store:{name}:start -->", f"<!-- store:{name}:end -->"
        meta_path = plugin / "skills/configure-store/generator/fragments" / name / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        try:
            blocks = _marker_blocks(text)
            marker_error = None
        except ValueError as error:
            blocks = {}
            marker_error = str(error)
        scaffold = (project / "_shared/orchestrator-rules.md").is_file() if meta.get("scaffold") else None
        if name in blocks:
            installation_evidence = "instruction_marker"
            project_installation = "detected"
        elif scaffold:
            installation_evidence = "scaffold_artifact"
            project_installation = "detected"
        elif marker_error is not None and (start in text or end in text):
            installation_evidence = "malformed_instruction_marker"
            project_installation = "indeterminate"
        else:
            installation_evidence = "none"
            project_installation = "not_detected"
        return {"source": "fragment", "name": name, "flavor": flavor, "catalog_known": bool(meta_path.is_file()), "marker_start": start in text, "marker_end": end in text, "marker_structure_valid": marker_error is None, "marker_present": name in blocks, "marker_error": marker_error, "scaffold_detected": scaffold, "project_installation": project_installation, "installation_evidence": installation_evidence, "runtime_active": None, "applied": False}
    if action not in {"install", "remove", "enable", "disable"}:
        raise ValueError("Unsupported fragment action.")
    installing = action in {"install", "enable"}
    blocks = _marker_blocks(instruction.read_text(encoding="utf-8")) if instruction.is_file() else {}
    meta_path = plugin / "skills/configure-store/generator/fragments" / name / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    scaffold = bool(meta.get("scaffold"))
    if scaffold and installing and any(project.iterdir()):
        raise ValueError("A scaffold is only supported in an empty target by this manager; existing projects need the official installer workflow.")
    modified_block = False
    if installing and name in blocks:
        fragment = plugin / "skills/configure-store/generator/fragments" / name
        variant = fragment / "fragment.codex.md"
        source = variant if flavor == "codex" and variant.is_file() else fragment / "fragment.md"
        if source.is_file():
            body = source.read_text(encoding="utf-8").strip("\n")
            expected = f"<!-- store:{name}:start -->\n{body}\n<!-- store:{name}:end -->"
            modified_block = blocks[name] != expected
            if modified_block and not replace_modified_block:
                raise ValueError("Refusing to overwrite a modified or outdated instruction block; inspect it and use --replace-modified-block to replace it explicitly.")
    paths = _fragment_paths(plugin, project, name, flavor, installing)
    command = [sys.executable, str(script), "--target", str(project), "--flavor", flavor, "--pick" if installing else "--remove", name, "--yes"]
    preview = _run(command + ["--dry-run"])
    if preview.returncode:
        raise ValueError(f"Loadout preview rejected the request (exit {preview.returncode}):\n{preview.stdout}{preview.stderr}")
    result: dict[str, Any] = {"source": "fragment", "name": name, "action": action, "flavor": flavor, "project_root": str(project), "applied": False, "preview": preview.stdout.strip(), "replacing_modified_block": modified_block, "runtime_active": None}
    if not apply:
        return result
    before = _snapshot(paths) if not scaffold else {}
    try:
        completed = _run(command)
        if completed.returncode:
            raise RuntimeError(f"Loadout installer failed (exit {completed.returncode}):\n{completed.stdout}{completed.stderr}")
    except (OSError, RuntimeError) as error:
        if before:
            _restore(project, before)
            raise
        raise RuntimeError(f"{error}\nScaffold may have left partial files in {project}; inspect before retrying.") from error
    result.update({"applied": True, "output": completed.stdout.strip(), "note": "Instruction fragments take effect when loaded; guard runtime is a separate coach switch."})
    return result
