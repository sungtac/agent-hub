# Agent Hub implementation plan

## Status and scope

This is the approved implementation baseline. It preserves the previously agreed objectives: provider-independent orchestration, Codex/Claude/Antigravity role switching, shared but scoped skills and Loadout, LangGraph/LangMem/DeepAgents integration, knowledge governance, autonomous bounded improvement, and a complete code-review checklist.

No claim of "complete portability" is valid until the documented acceptance tests pass in the target environment.

## Non-negotiable operating policy

| Policy | Decision |
|---|---|
| Owner | The owner is the sole authorized operator. |
| Retention | Retain data until the owner explicitly requests deletion. |
| Sharing | External sharing is disabled by default. |
| Classification | Every artifact is `company`, `personal`, `shared`, or `quarantine`; unknown scope is `quarantine`. |
| Cross-scope access | Denied by default. Promotion to `shared` requires an explicit owner command and sensitivity check. |
| Global configuration | The hub is the source of truth; it must not silently overwrite native global Codex, Claude, or Antigravity configuration. |
| Automatic improvement | Allowed only inside the bounded policy described below. |

## Architecture

```text
Natural-language request
  -> harness control plane
     -> provider adapter (Codex / Claude / Antigravity)
     -> logical role (orchestrator / implementer / critic / researcher / improver)
  -> task ledger (authoritative)
  -> LangGraph checkpoint (resumability cache)
  -> Skill Registry + Loadout compiler
  -> Knowledge Mesh + LangMem candidate memory
```

The file-based task ledger is the authoritative record for tasks, approvals, decisions, handoffs, provider leases, and material outputs. LangGraph checkpoints and search indexes are rebuildable operational state, not competing truth sources.

The managed root is `/home/sungt/agent-hub`. Existing `/home/sungt/agent-skills` remains an independent source repository and is registered rather than copied.

All executable paths are resolved from hub configuration rather than hard-coded into the runtime. The displayed managed-root path is the current local deployment value, not a portability contract.

## Provider contract

Each provider must pass the same conformance suite before it may execute a role:

1. non-interactive invocation;
2. normalized structured result and error collection;
3. cancellation and timeout handling;
4. capability discovery;
5. file and skill permission projection;
6. usage-limit or unavailable-state classification;
7. task handoff acceptance;
8. stale-lease rejection after a role switch.

Provider states are `available`, `degraded`, `limited`, `unavailable`, and `unknown`. `unknown` is never selected automatically. A switch freezes dispatch, commits a handoff record, increments the task generation, validates target acceptance, and only then grants the new lease.

Role switching guarantees transfer of the task ledger, decisions, approvals, artifacts, and current work state. It does not claim to transfer a provider's hidden proprietary conversation state. Provider subprocesses are owner-delegated executors, not independent principals; the harness supplies only the scope and capability required by the assigned task.

## Loadout and skills

Loadout is a central configuration compiler with these layers:

```text
platform baseline -> company or personal profile -> project overlay -> temporary task overlay
```

Every module exposes separate states: `installed`, `enabled`, `mounted`, and `available`. The registry records scope, version, provider compatibility, required tools, permissions, cost tier, validation state, and activation tags.

Skills are classified as `portable`, `conditional`, `provider-specific`, or `hosted-only`. A shared registry must never advertise a skill as executable by a provider until its adapter and requirements have passed validation.

The global-on behavior is implemented through a hub launcher and generated, provider-native project overlays. It must not depend on silently editing unrelated global provider settings. A project launched outside the managed entrypoint is explicitly reported as unmanaged rather than assumed to inherit the Loadout.

Skill search first reads only a compact local index. It ranks at most three candidates, then reads the selected `SKILL.md`, then only the references or scripts required by that skill. Skills are visible to all logical agents, but execute only through a provider adapter that satisfies their contract.

## Knowledge Mesh and deletion

Knowledge is partitioned into `company`, `personal`, `shared`, and `quarantine`. Human-readable Markdown is the source-grounded knowledge mesh; LangMem stores operational memories and improvement candidates.

Deletion must cover the primary artifact, indexes, LangMem records, caches, checkpoints, and encrypted backup lifecycle. A tombstone contains the deletion receipt and scope, not the deleted content. Backup purging or data-key retirement is tracked until complete.

## Bounded autonomous improvement

The improvement observer is event-driven and can run in `interactive`, `scheduled`, or verified `service` mode. It observes successful work, repeated failures, review findings, usage anomalies, and explicit owner requests.

It may automatically promote only low-risk, measurable changes after sandbox evaluation, regression checks, and baseline comparison. It may not change:

- access control, scope boundaries, sharing, or deletion policy;
- provider-switch safety rules;
- spending and token limits;
- encryption, backup, or recovery policy;
- its own attempt, rollback, cooldown, or parallelism limits.

Automatic promotion tiers are: A (documentation, indexes, and metadata), B (tested hub-owned code and skills), and C (registered project code). Tier C is disabled until its project explicitly has an owner-authored `autonomy` policy and a passing project test baseline. This keeps automatic improvement from silently changing an unregistered codebase.

Initial limits are one parallel improvement, three attempts per candidate, five related changes per day, two rollbacks per area, and a 240-minute cooldown after failure. Two consecutive rollbacks freeze automatic promotion for that area.

## Code-review checklist

The command phrase `점검 리스트` creates a read-only coverage manifest before review. It records each discovered file, hash, inclusion/exclusion reason, review state, and review outcome. Review work is partitioned by language, module, and risk. The final report distinguishes complete, excluded, failed, and deferred coverage.

The first review target is this hub. Other projects must be explicitly registered before they are scanned.

## Delivery phases and acceptance gates

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | Environment contract, compatibility matrix, dependency lock strategy | Current/target differences documented and classified. |
| 1 | Repository skeleton, schemas, runtime-data boundaries, recovery design | Fresh initialization is repeatable and creates no global-provider changes. |
| 2 | Task ledger, approval records, leases, audit events | Concurrent and interrupted task tests pass. |
| 3 | Codex adapter | Codex task execution, error normalization, cancellation, and handoff tests pass. |
| 4 | Read-only `점검 리스트` | Coverage manifest identifies every inclusion/exclusion outcome. |
| 5 | LangGraph flow and idempotent receipts | Resume produces no duplicated material action. |
| 6 | Skill Registry, Loadout compiler, generated manuals | Existing Codex skills register without copying or conflict. |
| 7 | Knowledge Mesh, deletion, and recovery tests | Scope denial, promotion, deletion propagation, and recovery tests pass. |
| 8 | Claude adapter | Codex-to-Claude and Claude-to-Codex orchestrator transitions pass. |
| 9 | DeepAgents and LangMem | Skill execution and candidate-memory boundaries pass. |
| 10 | Improvement observer | Sandbox promotion, rollback, cooldown, and immutable-policy tests pass. |
| 11 | Antigravity adapter | Three-provider capability and handoff tests pass. |
| 12 | Operational hardening | Restart, sleep, network loss, usage limit, backup, and full-review tests pass. |

No later phase is complete merely because files exist. Progress is earned only by its gate passing.

## Progress accounting and user experience

Implementation percentage is the weighted total of verified acceptance gates only. Planning documents, generated files, or partially written code earn no completion percentage until their declared gate passes.

The owner-facing commands and generated manuals are Korean-first and natural-language oriented. Advanced provider, scope, and recovery details remain inspectable on request but are not required for ordinary operation.

## Dependency provenance

Before a dependency is installed, the compatibility matrix must record the package or CLI source, version range, license, checksum or immutable release reference where available, minimum runtime, tested provider adapters, and rollback method. The dependency lock is committed only after this evidence exists.
