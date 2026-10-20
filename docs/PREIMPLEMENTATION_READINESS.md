# Pre-implementation readiness

## Intent

This document prepares implementation without changing provider accounts, native global configuration, user knowledge, or installed tools. It applies the user's coding principles to the approved plan.

## Confirmed current baseline

| Area | Observed state | Implementation consequence |
|---|---|---|
| OS | WSL2 Linux | Use `/home/sungt` as the authoritative hub location. |
| Existing code | `/home/sungt/agent-skills` only | Preserve and register it; do not merge or duplicate it. |
| Codex | CLI installed | Start provider development with Codex. |
| Python | 3.10 installed | Do not begin DeepAgents runtime work until an isolated Python 3.12 environment is available. |
| Claude / Antigravity | Not yet locally detected | Treat as planned adapters until installed and conformance-tested. |
| Loadout / Knot / LangGraph | Not yet locally detected | Build contracts first; install only version-locked dependencies later. |
| Docker / uv / pipx | Not currently detected | Do not make them prerequisites for phase 0. |

## Preparation decisions

1. Code, user data, secrets, generated provider configuration, and test artifacts have separate storage boundaries.
2. Provider-native files are generated or mounted per runtime; the hub never relies on copying a Codex-only skill into another runtime.
3. All persistent contracts require an explicit schema version.
4. Any future installation records source, version, checksum where available, license, and validation result.
5. Each phase must have one small observable behavior, a failure path, and a recovery method before the next phase starts.

## First implementation unit

The first code change will be a minimal environment-contract validator. It will only inspect and report:

- operating system, architecture, filesystem and mount behavior;
- Python and provider CLI availability and versions;
- writable hub and runtime-data paths;
- required command availability;
- unsupported or unknown conditions.

It must not install packages, log credentials, modify provider configuration, invoke a provider model, or create user knowledge.

Completion criterion: the validator produces a versioned, redacted report and has tests for success, missing tools, unwritable paths, and unknown provider states.

## Deferred until explicit implementation phase

- Python 3.12 installation and dependency installation;
- Claude and Antigravity installation or login;
- service registration or background scheduling;
- encryption-key creation and backup provisioning;
- global-provider configuration changes;
- scanning projects other than `agent-hub`.
