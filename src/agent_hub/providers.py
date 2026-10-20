"""Provider adapters with a narrow, normalised execution contract."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


MAX_DIAGNOSTIC_CHARACTERS = 10_000
PROFILE_ENVIRONMENT_KEYS = {
    "codex": ("CODEX_HOME",),
    "claude": ("CLAUDE_CONFIG_DIR",),
    "antigravity": ("ANTIGRAVITY_HOME", "AGY_HOME"),
}


@dataclass(frozen=True)
class ProviderCapability:
    provider: str
    state: str
    executable: str | None
    version: str | None
    detail: str | None


@dataclass(frozen=True)
class ExecutionResult:
    provider: str
    status: str
    return_code: int | None
    final_message: str | None
    events: tuple[dict[str, Any], ...]
    diagnostic: str | None

    def serialise(self) -> dict[str, Any]:
        return asdict(self)


class ProviderExecutionError(RuntimeError):
    """Raised for invalid local execution requests before a provider starts."""


def truncate(value: str) -> str:
    return value[:MAX_DIAGNOSTIC_CHARACTERS] if len(value) > MAX_DIAGNOSTIC_CHARACTERS else value


def platform_command(command: list[str]) -> list[str]:
    """Return a direct executable invocation without introducing a shell.

    ``cmd.exe`` reparses metacharacters after Python has constructed argv.  A
    prompt is untrusted input, so batch launchers cannot satisfy the adapter's
    no-shell contract.  Use a native executable on Windows instead.
    """

    # Do not construct ``Path`` here.  Tests may exercise the Windows policy
    # while running on a POSIX host, where ``Path`` would try to instantiate a
    # WindowsPath and fail before the unsafe launcher is rejected.
    if os.name == "nt" and command[0].lower().endswith((".cmd", ".bat")):
        raise ProviderExecutionError("Windows batch launchers are not supported; configure a native provider executable.")
    return command


def provider_environment(provider: str, inherited: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child-process environment without other providers' profile overrides.

    Credentials are never copied into Hub state.  Native CLI logins remain in
    their own existing client stores; this guard prevents an explicit profile
    override for one CLI from accidentally being inherited by another.
    """

    if provider not in PROFILE_ENVIRONMENT_KEYS:
        raise ValueError(f"Unsupported provider profile: {provider}")
    environment = dict(os.environ if inherited is None else inherited)
    for other_provider, keys in PROFILE_ENVIRONMENT_KEYS.items():
        if other_provider != provider:
            for key in keys:
                environment.pop(key, None)
    return environment


class CodexAdapter:
    """Codex CLI adapter using read-only, ephemeral non-interactive invocations."""

    provider = "codex"

    def __init__(self, executable: str | None = None, environment: Mapping[str, str] | None = None) -> None:
        self.executable = executable or shutil.which("codex")
        self.environment = provider_environment(self.provider, environment)

    def discover(self) -> ProviderCapability:
        if self.executable is None:
            return ProviderCapability(self.provider, "unknown", None, None, "Codex CLI was not detected.")
        try:
            completed = subprocess.run(
                platform_command([self.executable, "--version"]),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=self.environment,
            )
        except (OSError, ProviderExecutionError, subprocess.TimeoutExpired) as error:
            return ProviderCapability(self.provider, "degraded", self.executable, None, truncate(str(error)))
        if completed.returncode != 0:
            return ProviderCapability(self.provider, "degraded", self.executable, None, truncate(completed.stderr))
        return ProviderCapability(self.provider, "available", self.executable, completed.stdout.strip(), None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        if self.executable is None:
            raise ProviderExecutionError("Codex CLI was not detected.")
        if not prompt.strip():
            raise ProviderExecutionError("Prompt must not be empty.")
        directory = working_directory.expanduser().resolve(strict=False)
        if not directory.is_dir():
            raise ProviderExecutionError(f"Working directory does not exist: {directory}")
        with tempfile.TemporaryDirectory(prefix="agent-hub-codex-") as temporary_directory:
            final_message_path = Path(temporary_directory) / "final-message.txt"
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--json",
                "--sandbox",
                "read-only",
                "--cd",
                str(directory),
                "--output-last-message",
                str(final_message_path),
                prompt,
            ]
            try:
                process = subprocess.Popen(platform_command(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment)
            except (OSError, ProviderExecutionError) as error:
                return ExecutionResult(self.provider, "failed", None, None, (), truncate(str(error)))
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                return ExecutionResult(
                    provider=self.provider,
                    status="timed_out",
                    return_code=process.returncode,
                    final_message=None,
                    events=parse_events(stdout),
                    diagnostic=truncate(stderr),
                )
            final_message = final_message_path.read_text(encoding="utf-8", errors="replace") if final_message_path.is_file() else None
            status = "succeeded" if process.returncode == 0 else classify_failure(stdout, stderr)
            if status == "succeeded" and not (final_message and final_message.strip()):
                status = "empty"
            return ExecutionResult(
                provider=self.provider,
                status=status,
                return_code=process.returncode,
                final_message=final_message,
                events=parse_events(stdout),
                diagnostic=truncate(stderr) or None,
            )


class TemplateCliAdapter:
    """Conservative adapter for a provider whose CLI contract is explicitly supplied.

    It uses argv directly (never a shell), accepts a prompt placeholder only as
    one argument, and remains unavailable until the executable is present.
    This lets a newly installed Claude/Antigravity client prove its contract
    with a fake CLI before it is trusted for orchestration.
    """

    def __init__(self, provider: str, executable: str | None, command_template: tuple[str, ...], environment: Mapping[str, str] | None = None) -> None:
        if not provider or not command_template or any(any(marker not in {"prompt", "cwd"} for marker in _placeholders(item)) for item in command_template):
            raise ValueError("Invalid provider template.")
        if sum(item.count("{prompt}") for item in command_template) != 1:
            raise ValueError("Provider template must contain exactly one {prompt} argument.")
        self.provider = provider
        self.executable = executable or shutil.which(provider)
        self.command_template = command_template
        self.environment = provider_environment(provider, environment)

    def discover(self) -> ProviderCapability:
        if self.executable is None:
            return ProviderCapability(self.provider, "unknown", None, None, "Provider CLI was not detected.")
        try:
            completed = subprocess.run(platform_command([self.executable, "--version"]), capture_output=True, text=True, timeout=10, check=False, env=self.environment)
        except (OSError, ProviderExecutionError, subprocess.TimeoutExpired) as error:
            return ProviderCapability(self.provider, "degraded", self.executable, None, truncate(str(error)))
        if completed.returncode != 0:
            return ProviderCapability(self.provider, "degraded", self.executable, None, truncate(completed.stderr))
        return ProviderCapability(self.provider, "available", self.executable, completed.stdout.strip(), None)

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        if self.executable is None or not prompt.strip():
            raise ProviderExecutionError("Provider CLI was not detected or prompt was empty.")
        directory = working_directory.expanduser().resolve(strict=False)
        if not directory.is_dir():
            raise ProviderExecutionError(f"Working directory does not exist: {directory}")
        command = [self.executable, *[part.replace("{prompt}", prompt).replace("{cwd}", str(directory)) for part in self.command_template]]
        try:
            completed = subprocess.run(platform_command(command), capture_output=True, text=True, timeout=timeout_seconds, check=False, cwd=directory, env=self.environment)
        except subprocess.TimeoutExpired as error:
            return ExecutionResult(self.provider, "timed_out", None, None, (), truncate((error.stderr or "") if isinstance(error.stderr, str) else ""))
        except (OSError, ProviderExecutionError) as error:
            return ExecutionResult(self.provider, "failed", None, None, (), truncate(str(error)))
        status = "succeeded" if completed.returncode == 0 else classify_failure(completed.stdout, completed.stderr)
        return ExecutionResult(self.provider, status, completed.returncode, completed.stdout.strip() or None, parse_events(completed.stdout), truncate(completed.stderr) or None)


class JsonCliAdapter(TemplateCliAdapter):
    """Explicit provider contract whose non-interactive result is JSON."""

    def __init__(self, provider: str, executable: str | None, command_template: tuple[str, ...], result_key: str, environment: Mapping[str, str] | None = None) -> None:
        super().__init__(provider, executable, command_template, environment)
        self.result_key = result_key

    def execute(self, prompt: str, working_directory: Path, timeout_seconds: float = 120.0) -> ExecutionResult:
        result = super().execute(prompt, working_directory, timeout_seconds)
        if result.status != "succeeded":
            return result
        try:
            payload = json.loads(result.final_message or "")
            message = payload.get(self.result_key)
            if isinstance(message, str) and message.strip():
                return ExecutionResult(result.provider, result.status, result.return_code, message, result.events, result.diagnostic)
            return ExecutionResult(result.provider, "empty", result.return_code, None, result.events, result.diagnostic or "Provider returned no final response.")
        except json.JSONDecodeError:
            return ExecutionResult(result.provider, "failed", result.return_code, None, result.events, result.diagnostic or "Provider returned invalid structured output.")


def claude_adapter(executable: str | None = None) -> JsonCliAdapter:
    # Hub calls are bounded, read-only routing/review turns.  Explicit low
    # effort prevents a simple receipt check from consuming the entire wall
    # clock budget; substantive design work remains a Loadout worker task.
    return JsonCliAdapter("claude", executable, ("--print", "--output-format", "json", "--permission-mode", "plan", "--permission-prompts", "none", "--effort", "low", "--no-session-persistence", "--safe-mode", "--restricted", "{prompt}"), "result")


def antigravity_adapter(executable: str | None = None) -> JsonCliAdapter:
    return JsonCliAdapter("antigravity", antigravity_executable(executable), ("--output-format", "json", "--mode", "plan", "--sandbox", "--effort", "low", "--print={prompt}"), "response")


def antigravity_executable(executable: str | None = None) -> str | None:
    """Return only the mandatory local read-only Antigravity wrapper.

    ``executable`` is intentionally ignored.  A caller-provided raw binary
    would turn a review route into a write-capable escape hatch.
    """

    return shutil.which("agy-readonly-review")


def _placeholders(value: str) -> tuple[str, ...]:
    import re
    # JSON schema arguments legitimately contain braces. Only identifier-shaped
    # placeholders participate in template substitution or validation.
    return tuple(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", value))


def parse_events(stdout: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return tuple(events)


def classify_failure(stdout: str, stderr: str) -> str:
    diagnostic = f"{stdout}\n{stderr}".lower()
    if any(marker in diagnostic for marker in ("rate limit", "usage limit", "quota", "limit reached")):
        return "limited"
    if any(marker in diagnostic for marker in ("authentication", "unauthorized", "not logged", "network is unreachable", "network unreachable", "connection refused", "temporary failure in name resolution", "dns")):
        return "unavailable"
    return "failed"


def limited_retry_after_seconds(*messages: str) -> int | None:
    """Extract a provider quota-reset delay without trusting its exact wording.

    The known Antigravity diagnostic is ``Resets in 165h18m41s``.  Keep this
    parser deliberately narrow: an unrelated number must never turn a short
    provider cooldown into a multi-day suppression.
    """

    diagnostic = "\n".join(messages).lower()
    match = re.search(
        r"\breset(?:s)?\s+in\s+"
        r"(?:(?P<days>\d+)\s*d(?:ays?)?\s*)?"
        r"(?:(?P<hours>\d+)\s*h(?:ours?)?\s*)?"
        r"(?:(?P<minutes>\d+)\s*m(?:in(?:utes?)?)?\s*)?"
        r"(?:(?P<seconds>\d+)\s*s(?:ec(?:onds?)?)?)?",
        diagnostic,
    )
    if match is None or not any(match.group(name) for name in ("days", "hours", "minutes", "seconds")):
        return None
    factors = {"days": 86_400, "hours": 3_600, "minutes": 60, "seconds": 1}
    return sum(int(match.group(name) or 0) * factor for name, factor in factors.items())
