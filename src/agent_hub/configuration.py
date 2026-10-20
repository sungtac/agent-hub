"""Local Hub configuration and safe runtime-boundary initialization."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_SCHEMA_VERSION = 1
CONFIG_RELATIVE_PATH = Path("config/hub.json")
RUNTIME_DIRECTORIES = (
    Path("runtime"),
    Path("runtime/state"),
    Path("runtime/tasks"),
    Path("runtime/knowledge"),
    Path("runtime/reports"),
    Path("runtime/backups"),
)


class ConfigurationError(RuntimeError):
    """Raised when a Hub root cannot be initialized safely."""


@dataclass(frozen=True)
class HubConfiguration:
    schema_version: int
    owner_mode: str
    sharing: str
    default_scope: str
    runtime_directories: tuple[str, ...]


def default_configuration() -> HubConfiguration:
    return HubConfiguration(
        schema_version=CONFIG_SCHEMA_VERSION,
        owner_mode="owner-only",
        sharing="disabled",
        default_scope="quarantine",
        # Configuration is data, rather than a host-path serialization.  Keep
        # its separators stable so the same hub layout means the same thing on
        # Windows, macOS, and Linux.
        runtime_directories=tuple(path.as_posix() for path in RUNTIME_DIRECTORIES),
    )


def _ensure_directory(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise ConfigurationError(f"Expected directory but found file: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _validate_runtime_directory(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or path.parts[0] != "runtime":
        raise ConfigurationError(f"Unsafe runtime directory: {value!r}")
    return value


def initialise_hub(root: Path) -> Path:
    """Create local Hub boundaries once without touching provider configuration."""

    hub_root = root.expanduser().resolve(strict=False)
    if not hub_root.is_dir():
        raise ConfigurationError(f"Hub root must already exist: {hub_root}")
    config_path = hub_root / CONFIG_RELATIVE_PATH
    if config_path.exists():
        configuration = load_configuration(hub_root)
    else:
        _ensure_directory(config_path.parent)
        configuration = default_configuration()
        try:
            with config_path.open("x", encoding="utf-8") as stream:
                json.dump(asdict(configuration), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
        except OSError as error:
            raise ConfigurationError(f"Could not write configuration: {config_path}") from error
    # Version-1 configurations may not list the runtime root itself. Keep the
    # containing boundary private for both old and newly created configurations.
    _ensure_directory(hub_root / "runtime")
    for value in configuration.runtime_directories:
        _ensure_directory(hub_root / _validate_runtime_directory(value))
    return config_path


def load_configuration(root: Path) -> HubConfiguration:
    """Load and minimally validate a configuration without accepting unknown schemas."""

    config_path = root.expanduser().resolve(strict=False) / CONFIG_RELATIVE_PATH
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Could not read configuration: {config_path}") from error
    if raw.get("schema_version") != CONFIG_SCHEMA_VERSION:
        raise ConfigurationError("Unsupported configuration schema version.")
    try:
        configuration = HubConfiguration(
            schema_version=raw["schema_version"],
            owner_mode=raw["owner_mode"],
            sharing=raw["sharing"],
            default_scope=raw["default_scope"],
            runtime_directories=tuple(raw["runtime_directories"]),
        )
    except (KeyError, TypeError) as error:
        raise ConfigurationError("Invalid configuration shape.") from error
    if configuration.owner_mode != "owner-only" or configuration.sharing != "disabled":
        raise ConfigurationError("Unsupported ownership or sharing policy.")
    if configuration.default_scope != "quarantine":
        raise ConfigurationError("Unsupported default scope.")
    validated_directories = tuple(_validate_runtime_directory(value).replace("\\", "/") for value in configuration.runtime_directories)
    return HubConfiguration(
        schema_version=configuration.schema_version,
        owner_mode=configuration.owner_mode,
        sharing=configuration.sharing,
        default_scope=configuration.default_scope,
        runtime_directories=validated_directories,
    )
