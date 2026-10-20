"""Small, dependency-free command-line entrypoint for bootstrap operations."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Sequence

from .compatibility import assess, serialise_results
from .backup import create_backup, restore_backup, verify_backup
from .configuration import ConfigurationError, initialise_hub, load_configuration
from .environment import build_report
from .ledger import LedgerError, TaskLedger
from .knowledge import KnowledgeStore
from .memory_candidates import MemoryCandidateStore, extract_langmem_candidates
from .improvement import ImprovementQueue
from .observer import ImprovementObserver
from .multiagent_improvement import ReviewCycleError, ReviewReturnedToDesign, validate_ledger_review
from .improvement_review import ROLE_PROVIDERS, run_missing_reviews, run_review
from .discovery import DiscoveryStore, discover, draft, source_fingerprint
from .sandbox import SandboxImprovementRunner
from .scheduler import ObserverScheduler
from .subscription_memory import ALLOWED_TRIGGERS, antigravity_memory_adapter, extract_routed_subscription_candidates, extract_subscription_candidates, subscription_memory_adapters
from .loadout import LoadoutCatalog
from .loadout_manager import installed_plugin_root, manage_fragment, manage_guard, manage_hub
from .loadout_bridge import LoadoutBridgeError, bind_task, migrate_tasks, sync_task
from .review import build_manifest, write_new_manifest
from .routing import ProviderRouter
from .provider_gateway import ProviderGateway
from .natural_language import execute_safe, interpret, serialise
from .skills import SkillRegistry
from .providers import CodexAdapter, antigravity_adapter, claude_adapter
from .subscription_policy import load_subscription_bridge_policy, save_subscription_bridge_policy, subscription_bridge_policy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-hub")
    subparsers = parser.add_subparsers(dest="command", required=True)
    environment = subparsers.add_parser("environment", help="Read-only environment inspection")
    environment.add_argument("action", choices=("check", "validate"))
    environment.add_argument(
        "--hub-root",
        type=Path,
        default=Path.cwd(),
        help="Hub directory to inspect; no directory is created.",
    )
    environment.add_argument(
        "--output",
        type=Path,
        help="Write the report to a new file; existing files are never overwritten.",
    )
    initialise = subparsers.add_parser("init", help="Create local Hub runtime boundaries")
    initialise.add_argument(
        "--hub-root",
        type=Path,
        default=Path.cwd(),
        help="Existing Hub root; provider configuration is never modified.",
    )
    task = subparsers.add_parser("task", help="Operate the local append-only task ledger")
    task.add_argument("--hub-root", type=Path, default=Path.cwd())
    task_subparsers = task.add_subparsers(dest="task_action", required=True)
    create = task_subparsers.add_parser("create")
    create.add_argument("--title", required=True)
    create.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    show = task_subparsers.add_parser("show")
    show.add_argument("task_id")
    acquire = task_subparsers.add_parser("lease-acquire")
    acquire.add_argument("task_id")
    acquire.add_argument("--provider", required=True)
    acquire.add_argument("--role", required=True)
    release = task_subparsers.add_parser("lease-release")
    release.add_argument("task_id")
    release.add_argument("--lease-id", required=True)
    release.add_argument("--generation", type=int, required=True)
    release.add_argument("--reason", required=True)
    recover = task_subparsers.add_parser("lease-recover", help="Release an owner-confirmed abandoned lease after its grace period")
    recover.add_argument("task_id")
    recover.add_argument("--stale-after-seconds", required=True, type=int)
    approval = task_subparsers.add_parser("approve")
    approval.add_argument("task_id")
    approval.add_argument("--subject", required=True)
    approval.add_argument("--decision", required=True, choices=("approved", "rejected"))
    complete_task = task_subparsers.add_parser("complete", help="Close only after durable verification receipts exist")
    complete_task.add_argument("task_id")
    complete_task.add_argument("--evidence", required=True)
    review = subparsers.add_parser("review", help="Read-only code-review preparation")
    review_subparsers = review.add_subparsers(dest="review_action", required=True)
    checklist = review_subparsers.add_parser("checklist")
    checklist.add_argument("--project-root", type=Path, required=True)
    checklist.add_argument("--output", type=Path, required=True)
    loadout = subparsers.add_parser("loadout", help="Manage the central provider-neutral Loadout")
    loadout.add_argument("--hub-root", type=Path, default=Path.cwd())
    loadout_subparsers = loadout.add_subparsers(dest="loadout_action", required=True)
    loadout_subparsers.add_parser("list")
    unified = loadout_subparsers.add_parser("catalog", help="Read Hub modules, Loadout catalogue entries, and optional project installation evidence")
    unified.add_argument("--plugin-root", type=Path, help="Explicit Loadout plugin installation root")
    unified.add_argument("--project-root", type=Path, help="Optional project for reporting Store markers and scaffold evidence")
    manage = loadout_subparsers.add_parser("manage", help="Preview or apply Hub, Loadout fragment, and guard runtime changes")
    manage.add_argument("--source", choices=("hub", "fragment", "guard-runtime"), required=True)
    manage.add_argument("--action", choices=("status", "install", "remove", "enable", "disable", "doctor"), required=True)
    manage.add_argument("--name", help="Hub module or Loadout fragment name")
    manage.add_argument("--project-root", type=Path, help="Required for Loadout fragments")
    manage.add_argument("--flavor", choices=("claude", "codex"), help="Required for Loadout fragments")
    manage.add_argument("--plugin-root", type=Path, help="Explicit Loadout plugin installation root")
    manage.add_argument("--replace-modified-block", action="store_true", help="Allow an install to replace a modified or outdated managed instruction block")
    manage.add_argument("--apply", action="store_true", help="Apply the change; without this flag preview only")
    verify_loadout = loadout_subparsers.add_parser("verify", help="Read-only verification of the three standard project overlays")
    verify_loadout.add_argument("--project-root", type=Path, required=True)
    toggle = loadout_subparsers.add_parser("set")
    toggle.add_argument("name")
    toggle.add_argument("--enabled", required=True, choices=("true", "false"))
    manual = loadout_subparsers.add_parser("manual")
    manual.add_argument("--output", type=Path, required=True)
    overlay = loadout_subparsers.add_parser("overlay")
    overlay.add_argument("--provider", required=True, choices=("codex", "claude", "antigravity"))
    overlay.add_argument("--project-root", type=Path, required=True)
    overlay.add_argument("--output", type=Path, required=True)
    overlay.add_argument("--replace-managed", action="store_true", help="Refresh only an existing verified Agent Hub-managed overlay")
    bind_loadout_task = loadout_subparsers.add_parser("bind-task", help="Create one Hub task and bind it to a fresh Loadout task")
    bind_loadout_task.add_argument("--project-root", type=Path, required=True)
    bind_loadout_task.add_argument("--task-name", required=True)
    bind_loadout_task.add_argument("--title", required=True)
    bind_loadout_task.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    migrate_loadout = loadout_subparsers.add_parser("migrate-tasks", help="Preview or one-way bind legacy Loadout tasks")
    migrate_loadout.add_argument("--project-root", type=Path, required=True)
    migrate_loadout.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    migrate_loadout.add_argument("--apply", action="store_true", help="Write missing bridge ids; without it only report")
    sync_loadout = loadout_subparsers.add_parser("sync-task", help="Render the Hub-owned receipt block into one bound Loadout task")
    sync_loadout.add_argument("--project-root", type=Path, required=True)
    sync_loadout.add_argument("--task-name", required=True)
    knowledge = subparsers.add_parser("knowledge", help="Operate scope-isolated local knowledge")
    knowledge.add_argument("--hub-root", type=Path, default=Path.cwd())
    knowledge_subparsers = knowledge.add_subparsers(dest="knowledge_action", required=True)
    for action in ("save", "update"):
        command = knowledge_subparsers.add_parser(action)
        command.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
        command.add_argument("--id", required=True)
        command.add_argument("--content", required=True)
    read = knowledge_subparsers.add_parser("read")
    read.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    read.add_argument("--id", required=True)
    delete = knowledge_subparsers.add_parser("delete")
    delete.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    delete.add_argument("--id", required=True)
    search = knowledge_subparsers.add_parser("search")
    search.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)
    promote = knowledge_subparsers.add_parser("promote")
    promote.add_argument("--source-scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    promote.add_argument("--target-scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    promote.add_argument("--id", required=True)
    memory_candidate = subparsers.add_parser("memory-candidate", help="Review isolated LangMem memory candidates")
    memory_candidate.add_argument("--hub-root", type=Path, default=Path.cwd())
    candidate_subparsers = memory_candidate.add_subparsers(dest="candidate_action", required=True)
    candidate_list = candidate_subparsers.add_parser("list")
    candidate_list.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    for action in ("promote", "reject"):
        command = candidate_subparsers.add_parser(action)
        command.add_argument("--id", required=True)
    extract = candidate_subparsers.add_parser("extract", help="Use the configured LangChain model to stage LangMem candidates")
    extract.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    extract.add_argument("--task-id", required=True)
    extract.add_argument("--evidence", required=True)
    extract.add_argument("--messages-file", type=Path, required=True)
    extract_cli = candidate_subparsers.add_parser("extract-cli", help="Stage reviewed memory candidates using the subscribed Antigravity CLI")
    extract_cli.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    extract_cli.add_argument("--task-id", required=True)
    extract_cli.add_argument("--evidence", required=True)
    extract_cli.add_argument("--trigger", required=True, choices=tuple(sorted(ALLOWED_TRIGGERS)))
    extract_cli.add_argument("--messages-file", type=Path, required=True)
    extract_cli.add_argument("--timeout-seconds", type=float, default=120.0)
    routed_extract = candidate_subparsers.add_parser("extract-routed-cli", help="Extract through subscribed CLIs with durable provider handoff")
    routed_extract.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    routed_extract.add_argument("--task-id", required=True)
    routed_extract.add_argument("--evidence", required=True)
    routed_extract.add_argument("--trigger", required=True, choices=tuple(sorted(ALLOWED_TRIGGERS)))
    routed_extract.add_argument("--messages-file", type=Path, required=True)
    routed_extract.add_argument("--provider", choices=("auto", "codex", "claude", "antigravity"), default="auto")
    routed_extract.add_argument("--timeout-seconds", type=float, default=120.0)
    candidate_subparsers.add_parser("model-status")
    backup = subparsers.add_parser("backup", help="Create, verify, or restore local Hub backups")
    backup.add_argument("--hub-root", type=Path, default=Path.cwd())
    backup_subparsers = backup.add_subparsers(dest="backup_action", required=True)
    backup_create = backup_subparsers.add_parser("create")
    backup_create.add_argument("--output", type=Path, required=True)
    backup_verify = backup_subparsers.add_parser("verify")
    backup_verify.add_argument("--archive", type=Path, required=True)
    backup_restore = backup_subparsers.add_parser("restore")
    backup_restore.add_argument("--archive", type=Path, required=True)
    backup_restore.add_argument("--apply", action="store_true", help="Apply the verified snapshot; otherwise preview only.")
    improvement = subparsers.add_parser("improvement", help="Queue and run one bounded automatic improvement")
    improvement.add_argument("--hub-root", type=Path, default=Path.cwd())
    improvement_subparsers = improvement.add_subparsers(dest="improvement_action", required=True)
    observe = improvement_subparsers.add_parser("observe")
    observe.add_argument("--area", required=True)
    observe.add_argument("--summary", required=True)
    observe.add_argument("--evidence", required=True)
    observe.add_argument("--changes-file", type=Path, required=True, help="New JSON object of permitted relative source paths to full replacement text")
    observe.add_argument("--task-id", help="Hub task with recorded Codex and Claude review receipts")
    improvement_review = improvement_subparsers.add_parser("review", help="Run one read-only Codex or Claude review and attest it in the ledger")
    improvement_review.add_argument("--task-id", required=True)
    improvement_review.add_argument("--role", required=True, choices=tuple(ROLE_PROVIDERS))
    improvement_review.add_argument("--changes-file", type=Path, required=True)
    improvement_review.add_argument("--timeout-seconds", type=float, default=120.0)
    discovery = improvement_subparsers.add_parser("discover-once", help="Run one read-only Codex improvement finding scan")
    discovery.add_argument("--timeout-seconds", type=float, default=120.0)
    draft_improvement = improvement_subparsers.add_parser("draft", help="Create one read-only Claude proposal from a stored finding")
    draft_improvement.add_argument("--finding-id", required=True)
    draft_improvement.add_argument("--timeout-seconds", type=float, default=120.0)
    auto_once = improvement_subparsers.add_parser("auto-once", help="Run one bounded discovery-to-promotion cycle")
    auto_once.add_argument("--timeout-seconds", type=float, default=120.0)
    improvement_subparsers.add_parser("run-once")
    improvement_subparsers.add_parser("status", help="Show pending improvements and approval subjects")
    schedule = improvement_subparsers.add_parser("schedule")
    schedule.add_argument("action", choices=("status", "enable", "disable"))
    schedule.add_argument("--every-minutes", type=int, default=10)
    natural = subparsers.add_parser("natural", help="Interpret a Korean owner request without executing ambiguous actions")
    natural.add_argument("--text", required=True)
    natural.add_argument("--mode", default="basic", choices=("basic", "operations", "development"))
    natural.add_argument("--hub-root", type=Path, default=Path.cwd())
    natural.add_argument("--apply", action="store_true", help="Execute only an explicit reversible safe intent")
    natural.add_argument("--project-root", type=Path, help="Required by the natural-language review checklist flow")
    natural.add_argument("--output", type=Path, help="New report or manual path for a natural-language flow")
    natural.add_argument("--query", help="Skill query for a natural-language search flow")
    natural.add_argument("--task-id", help="Required by the natural-language memory extraction flow")
    natural.add_argument("--scope", choices=("company", "personal", "shared", "quarantine"), help="Knowledge scope for the natural-language memory flow")
    natural.add_argument("--message", action="append", default=[], help="Conversation text to consider for an explicit memory request; repeatable")
    natural.add_argument("--provider", choices=("auto", "codex", "claude", "antigravity"), default="auto", help="Preferred subscribed provider for natural memory extraction")
    natural.add_argument("--instruction", help="Explicit continuation instruction required by the natural-language orchestrator switch flow")
    natural.add_argument("--switch-id", help="Unique owner-supplied ID for this switch; reuse it to prevent duplicate dispatch")
    natural.add_argument("--shared-handoff", action="store_true", help="Explicitly share and persist only the non-sensitive structured handoff record")
    natural.add_argument("--provider-timeout-seconds", type=float, default=120.0, help="Bound one subscribed provider invocation; must be between 1 and 120 seconds")
    natural.add_argument("--strict-provider", action="store_true", help="For an independent review, record a selected provider failure instead of handing off")
    subscription_chat = subparsers.add_parser("subscription-chat", help="Inspect or explicitly enable the optional subscription CLI–LangChain bridge")
    subscription_chat.add_argument("--hub-root", type=Path, default=Path.cwd())
    subscription_chat_subparsers = subscription_chat.add_subparsers(dest="subscription_chat_action", required=True)
    subscription_chat_subparsers.add_parser("status")
    subscription_set = subscription_chat_subparsers.add_parser("set")
    subscription_set.add_argument("--enabled", required=True, choices=("true", "false"))
    subscription_set.add_argument("--provider", action="append", choices=("codex", "claude", "antigravity"))
    subscription_set.add_argument("--max-calls-per-task", type=int)
    subscription_set.add_argument("--timeout-seconds", type=float)
    gateway = subparsers.add_parser("gateway", help="Reserve and complete Loadout external dispatch receipts")
    gateway.add_argument("--hub-root", type=Path, default=Path.cwd())
    gateway_subparsers = gateway.add_subparsers(dest="gateway_action", required=True)
    reserve_external = gateway_subparsers.add_parser("reserve-external")
    reserve_external.add_argument("--task-id", required=True)
    reserve_external.add_argument("--channel", required=True)
    reserve_external.add_argument("--maximum", type=int, default=1)
    reserve_external.add_argument("--fingerprint", required=True)
    reserve_external.add_argument("--provider", required=True)
    complete_external = gateway_subparsers.add_parser("complete-external")
    complete_external.add_argument("--task-id", required=True)
    complete_external.add_argument("--dispatch-id", required=True)
    complete_external.add_argument("--status", required=True, choices=("succeeded", "failed", "timed_out", "empty", "scope_violation"))
    complete_external.add_argument("--return-code", type=int)
    skill = subparsers.add_parser("skill", help="List, search, register, and document the shared skill registry")
    skill.add_argument("--hub-root", type=Path, default=Path.cwd())
    skill_subparsers = skill.add_subparsers(dest="skill_action", required=True)
    skill_list = skill_subparsers.add_parser("list")
    skill_list.add_argument("--all-installed", action="store_true", help="Include read-only discovery of installed Codex, Claude plugin, and personal-directory skills")
    skill_subparsers.add_parser("catalog", help="Show one read-only catalog of registered and installed skills")
    skill_search = skill_subparsers.add_parser("search")
    skill_search.add_argument("--query", required=True)
    skill_search.add_argument("--all-installed", action="store_true", help="Search registered and discovered installed skills")
    skill_manual = skill_subparsers.add_parser("manual")
    skill_manual.add_argument("--output", type=Path, required=True)
    skill_manual.add_argument("--all-installed", action="store_true", help="Document registered and discovered installed skills")
    skill_register = skill_subparsers.add_parser("register")
    skill_register.add_argument("--source", type=Path, required=True)
    skill_register.add_argument("--scope", required=True, choices=("company", "personal", "shared", "quarantine"))
    skill_register.add_argument("--kind", required=True, choices=("portable", "conditional", "provider-specific", "hosted-only"))
    skill_register.add_argument("--cost-tier", required=True)
    skill_register.add_argument("--provider", action="append", default=[], metavar="NAME=STATE")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "environment" and args.action in {"check", "validate"}:
        report_data = build_report(args.hub_root)
        if args.action == "validate":
            report_data["compatibility"] = serialise_results(assess(report_data))
        report = json.dumps(report_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output is None:
            print(report, end="")
            return 0
        output = args.output.expanduser().resolve(strict=False)
        if not output.parent.is_dir():
            raise SystemExit(f"Output directory does not exist: {output.parent}")
        try:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(report)
        except FileExistsError as error:
            raise SystemExit(f"Refusing to overwrite existing report: {output}") from error
        return 0
    if args.command == "init":
        try:
            config_path = initialise_hub(args.hub_root)
        except ConfigurationError as error:
            raise SystemExit(str(error)) from error
        print(config_path)
        return 0
    if args.command == "task":
        try:
            root = args.hub_root.expanduser().resolve(strict=False)
            configuration = load_configuration(root)
            tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
            ledger = TaskLedger(root / tasks_directory)
            if args.task_action == "create":
                result = ledger.create_task(args.title, args.scope)
            elif args.task_action == "show":
                result = ledger.get_task(args.task_id)
            elif args.task_action == "lease-acquire":
                result = ledger.acquire_lease(args.task_id, args.provider, args.role)
            elif args.task_action == "lease-release":
                result = ledger.release_lease(args.task_id, args.lease_id, args.generation, args.reason)
            elif args.task_action == "lease-recover":
                result = ledger.recover_stale_lease(args.task_id, args.stale_after_seconds)
            elif args.task_action == "approve":
                result = ledger.record_approval(args.task_id, args.subject, args.decision)
                if args.decision == "approved" and args.subject.startswith("improvement-v1:"):
                    state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
                    observer = ImprovementObserver(
                        root / state_directory / "improvement-observer.json",
                        ImprovementQueue(root / state_directory / "improvements.json"),
                        SandboxImprovementRunner(Path(sys.executable)),
                        root,
                        root / "runtime" / "improvement-backups",
                        ledger,
                    )
                    result["improvement_promotion"] = observer.run_once(args.subject.split(":", 3)[1]) or {"status": "no_matching_pending_candidate"}
            elif args.task_action == "complete":
                result = ledger.complete_task(args.task_id, args.evidence)
            else:
                raise AssertionError("unreachable task parser state")
        except (ConfigurationError, LedgerError, StopIteration) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "review" and args.review_action == "checklist":
        try:
            manifest = build_manifest(args.project_root)
            write_new_manifest(manifest, args.output)
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(args.output.expanduser().resolve(strict=False))
        return 0
    if args.command == "loadout":
        try:
            root = args.hub_root.expanduser().resolve(strict=False)
            load_configuration(root)
            catalog = LoadoutCatalog(root / "config" / "loadout.json")
            if args.loadout_action != "manage":
                catalog.initialise()
            if args.loadout_action == "list":
                print(json.dumps([module.__dict__ for module in catalog.modules()], ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "catalog":
                plugin_root = args.plugin_root
                if plugin_root is None:
                    plugin_root = installed_plugin_root()
                print(json.dumps(catalog.unified_catalog(plugin_root, args.project_root), ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "manage":
                if args.action in {"status", "doctor"} and args.apply:
                    raise ValueError("Read-only status and doctor do not accept --apply.")
                if args.source == "hub":
                    if not args.name:
                        raise ValueError("Hub management requires --name.")
                    if args.project_root is not None or args.flavor or args.plugin_root is not None or args.replace_modified_block:
                        raise ValueError("Hub management does not accept fragment-specific options.")
                    result = manage_hub(catalog, args.name, args.action, args.apply)
                elif args.source == "guard-runtime":
                    if args.name or args.project_root is not None or args.flavor or args.plugin_root is not None or args.replace_modified_block:
                        raise ValueError("The guard-runtime source is global and does not accept fragment-specific options.")
                    result = manage_guard(args.action, args.apply)
                else:
                    if args.project_root is None:
                        raise ValueError("Fragment management requires --project-root.")
                    if args.action != "doctor" and (not args.name or not args.flavor):
                        raise ValueError("Fragment install, remove, enable, disable and status require --name and --flavor.")
                    result = manage_fragment(args.plugin_root or installed_plugin_root(), args.project_root, args.name, args.action, args.flavor, args.apply, args.replace_modified_block)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "verify":
                print(json.dumps(catalog.verify_overlays(args.project_root), ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "set":
                module = catalog.set_enabled(args.name, args.enabled == "true")
                print(json.dumps(module.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "manual":
                catalog.write_manual(args.output.expanduser().resolve(strict=False))
                print(args.output.expanduser().resolve(strict=False))
            elif args.loadout_action == "overlay":
                catalog.write_new_overlay(args.provider, args.project_root, args.output, args.replace_managed)
                print(args.output.expanduser().resolve(strict=False))
            elif args.loadout_action == "bind-task":
                tasks_directory = next(value for value in load_configuration(root).runtime_directories if value == "runtime/tasks")
                result = bind_task(TaskLedger(root / tasks_directory), args.project_root, args.task_name, args.title, args.scope)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "migrate-tasks":
                tasks_directory = next(value for value in load_configuration(root).runtime_directories if value == "runtime/tasks")
                result = migrate_tasks(TaskLedger(root / tasks_directory), args.project_root, args.scope, args.apply)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            elif args.loadout_action == "sync-task":
                tasks_directory = next(value for value in load_configuration(root).runtime_directories if value == "runtime/tasks")
                result = sync_task(TaskLedger(root / tasks_directory), args.project_root, args.task_name)
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                raise AssertionError("unreachable loadout parser state")
        except (ConfigurationError, LedgerError, LoadoutBridgeError, OSError, ValueError, RuntimeError, FileExistsError) as error:
            raise SystemExit(str(error)) from error
        return 0
    if args.command == "knowledge":
        try:
            root = args.hub_root.expanduser().resolve(strict=False)
            configuration = load_configuration(root)
            knowledge_directory = next(value for value in configuration.runtime_directories if value == "runtime/knowledge")
            store = KnowledgeStore(root / knowledge_directory)
            if args.knowledge_action == "save":
                result: object = str(store.save(args.scope, args.id, args.content))
            elif args.knowledge_action == "update":
                result = str(store.update(args.scope, args.id, args.content))
            elif args.knowledge_action == "read":
                result = store.read(args.scope, args.id)
            elif args.knowledge_action == "delete":
                result = str(store.delete(args.scope, args.id))
            elif args.knowledge_action == "search":
                result = store.search(args.scope, args.query, args.limit)
            elif args.knowledge_action == "promote":
                result = str(store.promote(args.source_scope, args.id, args.target_scope))
            else:
                raise AssertionError("unreachable knowledge parser state")
        except (ConfigurationError, ValueError, PermissionError, StopIteration) as error:
            raise SystemExit(str(error)) from error
        if isinstance(result, list):
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(result)
        return 0
    if args.command == "memory-candidate":
        try:
            from .integrations import IntegrationUnavailable, build_configured_memory_manager, configured_memory_model
            root = args.hub_root.expanduser().resolve(strict=False)
            configuration = load_configuration(root)
            state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
            knowledge_directory = next(value for value in configuration.runtime_directories if value == "runtime/knowledge")
            candidates = MemoryCandidateStore(root / state_directory / "memory-candidates.json")
            if args.candidate_action == "list":
                result = candidates.pending(args.scope)
            elif args.candidate_action == "promote":
                result = str(candidates.promote(args.id, KnowledgeStore(root / knowledge_directory)))
            elif args.candidate_action == "reject":
                result = candidates.reject(args.id)
            elif args.candidate_action == "model-status":
                result = {"configured": True, "model": configured_memory_model()}
            elif args.candidate_action == "extract":
                messages = json.loads(args.messages_file.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
                if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
                    raise ValueError("Messages file must contain a JSON list of message objects.")
                result = extract_langmem_candidates(
                    build_configured_memory_manager(args.scope),
                    messages,
                    "owner",
                    args.scope,
                    args.task_id,
                    args.evidence,
                    candidates,
                )
            elif args.candidate_action == "extract-cli":
                messages = json.loads(args.messages_file.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
                if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
                    raise ValueError("Messages file must contain a JSON list of message objects.")
                execution, result = extract_subscription_candidates(
                    antigravity_memory_adapter(),
                    trigger=args.trigger,
                    scope=args.scope,
                    task_id=args.task_id,
                    evidence=args.evidence,
                    messages=messages,
                    candidate_store=candidates,
                    working_directory=root,
                    timeout_seconds=args.timeout_seconds,
                )
                result = {"provider": execution.provider, "status": execution.status, "candidates": result}
            elif args.candidate_action == "extract-routed-cli":
                messages = json.loads(args.messages_file.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
                if not isinstance(messages, list) or not all(isinstance(item, dict) for item in messages):
                    raise ValueError("Messages file must contain a JSON list of message objects.")
                tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
                decision, execution, extracted = extract_routed_subscription_candidates(
                    TaskLedger(root / tasks_directory),
                    ProviderRouter(root / state_directory / "provider-health.json"),
                    subscription_memory_adapters(),
                    requested_provider=None if args.provider == "auto" else args.provider,
                    trigger=args.trigger,
                    scope=args.scope,
                    task_id=args.task_id,
                    evidence=args.evidence,
                    messages=messages,
                    candidate_store=candidates,
                    working_directory=root,
                    timeout_seconds=args.timeout_seconds,
                )
                result = {"provider": execution.provider, "status": execution.status, "fallback": decision.fallback, "candidates": extracted}
            else:
                raise AssertionError("unreachable memory-candidate parser state")
        except (ConfigurationError, IntegrationUnavailable, LedgerError, RuntimeError, ValueError, PermissionError, StopIteration) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) if isinstance(result, (list, dict)) else result)
        return 0
    if args.command == "backup":
        try:
            root = args.hub_root.expanduser().resolve(strict=True)
            load_configuration(root)
            if args.backup_action == "create":
                result: object = {"archive": str(create_backup(root, args.output))}
            elif args.backup_action == "verify":
                valid = verify_backup(args.archive)
                if not valid:
                    raise ValueError("Backup verification failed.")
                result = {"valid": True, "archive": str(args.archive.expanduser().resolve(strict=True))}
            elif args.backup_action == "restore":
                result = restore_backup(args.archive, root, apply=args.apply)
            else:
                raise AssertionError("unreachable backup parser state")
        except (ConfigurationError, OSError, ValueError, tarfile.TarError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "improvement":
        try:
            root = args.hub_root.expanduser().resolve(strict=True)
            configuration = load_configuration(root)
            state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
            state_root = root / state_directory
            queue = ImprovementQueue(state_root / "improvements.json")
            tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
            ledger = TaskLedger(root / tasks_directory)
            observer = ImprovementObserver(
                state_root / "improvement-observer.json",
                queue,
                SandboxImprovementRunner(Path(sys.executable)) if args.improvement_action in {"run-once", "auto-once"} else None,
                root,
                root / "runtime" / "improvement-backups",
                ledger,
            )
            if args.improvement_action == "observe":
                changes = json.loads(args.changes_file.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
                if not isinstance(changes, dict):
                    raise ValueError("Changes file must contain a JSON object.")
                try:
                    if not args.task_id:
                        raise ValueError("Observe requires --task-id with attested reviews.")
                    receipt = validate_ledger_review(ledger, args.task_id, changes)
                except ReviewReturnedToDesign as error:
                    result = {"status": "return_to_design", "stage": 2, "reason": str(error)}
                else:
                    result = {"candidate_id": observer.observe(args.area, args.summary, args.evidence, changes, receipt), "multiagent_review": receipt.serialise()}
            elif args.improvement_action == "review":
                changes = json.loads(args.changes_file.expanduser().resolve(strict=True).read_text(encoding="utf-8"))
                if not isinstance(changes, dict):
                    raise ValueError("Changes file must contain a JSON object.")
                adapters = {"codex": CodexAdapter(), "claude": claude_adapter()}
                result = run_review(ledger, args.task_id, args.role, changes, adapters[ROLE_PROVIDERS[args.role]], root, args.timeout_seconds).serialise()
            elif args.improvement_action == "discover-once":
                store = DiscoveryStore(state_root / "improvement-discovery.json")
                with store.locked():
                    execution, finding = discover(CodexAdapter(), root, args.timeout_seconds)
                    state = store._read()
                    if finding is not None:
                        state["findings"][finding["finding_id"]] = finding
                        store._write(state)
                result = {"execution": execution.serialise(), "finding": finding}
            elif args.improvement_action == "draft":
                store = DiscoveryStore(state_root / "improvement-discovery.json")
                with store.locked():
                    state = store._read()
                    finding = state["findings"].get(args.finding_id)
                    if not isinstance(finding, dict):
                        raise ValueError("Unknown improvement finding.")
                    execution, proposal = draft(claude_adapter(), root, finding, args.timeout_seconds)
                    if proposal is not None:
                        state["drafts"][proposal["proposal_id"]] = proposal
                        store._write(state)
                result = {"execution": execution.serialise(), "proposal": proposal}
            elif args.improvement_action == "auto-once":
                store = DiscoveryStore(state_root / "improvement-discovery.json")
                with store.locked():
                    state = store._read()
                    fingerprint = source_fingerprint(root)
                    # A failed/empty draft must be retryable.  Only a durable draft
                    # suppresses a repeated model discovery for unchanged inputs.
                    if any(item.get("fingerprint") == fingerprint and item.get("status") in {"pending", "awaiting_approval", "promoted"} for item in state["drafts"].values()):
                        result = {"status": "already_scanned", "fingerprint": fingerprint}
                    else:
                        retry = next((item for item in state["drafts"].values() if item.get("fingerprint") == fingerprint and item.get("status") == "review_pending"), None)
                        if isinstance(retry, dict):
                            proposal = retry
                            finding = state["findings"].get(proposal.get("finding_id"))
                            task_id = proposal.get("task_id")
                            if not isinstance(finding, dict) or not isinstance(task_id, str):
                                raise ValueError("Review retry state is incomplete.")
                            changes = proposal["changes"]
                        else:
                            execution, finding = discover(CodexAdapter(), root, args.timeout_seconds)
                            if finding is None:
                                result = {"status": "no_finding", "execution": execution.serialise()}
                                proposal = None
                            else:
                                state["findings"][finding["finding_id"]] = finding
                                drafted, proposal = draft(claude_adapter(), root, finding, args.timeout_seconds)
                                if proposal is None:
                                    store._write(state)
                                    result = {"status": "no_proposal", "finding": finding, "execution": drafted.serialise()}
                                else:
                                    task = ledger.create_task("automatic improvement: " + finding["summary"], "personal")
                                    task_id = task["task_id"]
                                    changes = proposal["changes"]
                                    proposal["status"] = "review_pending"
                                    proposal["task_id"] = task_id
                                    state["drafts"][proposal["proposal_id"]] = proposal
                                    store._write(state)
                        if proposal is not None:
                            adapters = {"codex": CodexAdapter(), "claude": claude_adapter()}
                            run_missing_reviews(ledger, task_id, changes, root, args.timeout_seconds, adapters)
                            try:
                                receipt = validate_ledger_review(ledger, task_id, changes)
                            except ReviewCycleError as error:
                                status = "return_to_design" if isinstance(error, ReviewReturnedToDesign) else "review_pending"
                                state["drafts"][proposal["proposal_id"]].update({"status": status, "review_diagnostic": str(error)})
                                store._write(state)
                                result = {"status": status, "task_id": task_id, "diagnostic": str(error)}
                            else:
                                candidate_id = observer.observe(finding["area"], finding["summary"], finding["evidence"], changes, receipt)
                                outcome = observer.run_once(candidate_id)
                                status = outcome["status"] if outcome else "awaiting_approval"
                                state["drafts"][proposal["proposal_id"]]["status"] = status
                                state["drafts"][proposal["proposal_id"]]["task_id"] = task_id
                                state["drafts"][proposal["proposal_id"]].pop("review_diagnostic", None)
                                store._write(state)
                                result = {"status": status, "candidate_id": candidate_id, "task_id": task_id, "outcome": outcome}
            elif args.improvement_action == "run-once":
                result = observer.run_once() or {"status": "idle"}
            elif args.improvement_action == "status":
                state = observer._load()
                items = state["observations"]
                result = {"counts": {status: sum(item.get("status") == status for item in items.values()) for status in ("pending", "awaiting_approval", "running", "promoted", "failed", "rejected", "deferred")}, "awaiting_approval": [{"candidate_id": identifier, "task_id": item.get("task_id"), "approval_subject": item.get("approval_subject")} for identifier, item in items.items() if item.get("status") == "awaiting_approval"]}
            elif args.improvement_action == "schedule":
                scheduler = ObserverScheduler(root, Path(sys.executable).with_name("agent-hub"))
                if args.action == "status":
                    result = {"enabled": scheduler.status_details(), "scheduler": "cron"}
                elif args.action == "enable":
                    scheduler.enable(args.every_minutes)
                    result = {"enabled": True, "scheduler": "cron", "every_minutes": args.every_minutes}
                else:
                    result = {"enabled": not scheduler.disable(), "scheduler": "cron"}
            else:
                raise AssertionError("unreachable improvement parser state")
        except (ConfigurationError, OSError, ValueError, PermissionError, RuntimeError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "subscription-chat":
        try:
            root = args.hub_root.expanduser().resolve(strict=True)
            configuration = load_configuration(root)
            state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
            state_root = root / state_directory
            current = load_subscription_bridge_policy(state_root)
            if args.subscription_chat_action == "status":
                result = {
                    "enabled": current.enabled,
                    "allowed_providers": sorted(current.allowed_providers),
                    "max_calls_per_task": current.max_calls_per_task,
                    "timeout_seconds": current.timeout_seconds,
                    "allow_tools": current.allow_tools,
                    "configured": (state_root / "subscription-langchain-policy.json").is_file(),
                }
            elif args.subscription_chat_action == "set":
                policy = subscription_bridge_policy(
                    {
                        "enabled": args.enabled == "true",
                        "allowed_providers": args.provider if args.provider else sorted(current.allowed_providers),
                        "max_calls_per_task": args.max_calls_per_task if args.max_calls_per_task is not None else current.max_calls_per_task,
                        "timeout_seconds": args.timeout_seconds if args.timeout_seconds is not None else current.timeout_seconds,
                    }
                )
                result = {"policy": str(save_subscription_bridge_policy(state_root, policy)), "enabled": policy.enabled, "allowed_providers": sorted(policy.allowed_providers)}
            else:
                raise AssertionError("unreachable subscription-chat parser state")
        except (ConfigurationError, OSError, ValueError, StopIteration) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "gateway":
        try:
            root = args.hub_root.expanduser().resolve(strict=True)
            configuration = load_configuration(root)
            tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
            gateway = ProviderGateway(TaskLedger(root / tasks_directory), ProviderRouter(root / "runtime" / "state" / "provider-health.json"), {})
            if args.gateway_action == "reserve-external":
                result = gateway.reserve_external(task_id=args.task_id, channel=args.channel, maximum=args.maximum, fingerprint=args.fingerprint, provider=args.provider)
            elif args.gateway_action == "complete-external":
                result = gateway.complete_external(task_id=args.task_id, dispatch_id=args.dispatch_id, status=args.status, return_code=args.return_code)
            else:
                raise AssertionError("unreachable gateway parser state")
        except (ConfigurationError, LedgerError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "natural":
        try:
            request = interpret(args.text, args.mode)
            if args.apply:
                root = args.hub_root.expanduser().resolve(strict=True)
                load_configuration(root)
                if request.intent in {"improvement_enable", "improvement_disable"}:
                    scheduler = ObserverScheduler(root, Path(sys.executable).with_name("agent-hub"))
                    result = execute_safe(request, lambda: scheduler.enable(10), scheduler.disable)
                elif request.intent in {"subscription_bridge_enable", "subscription_bridge_disable"}:
                    configuration = load_configuration(root)
                    state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
                    state_root = root / state_directory
                    current = load_subscription_bridge_policy(state_root)
                    policy = subscription_bridge_policy(
                        {
                            "enabled": request.intent == "subscription_bridge_enable",
                            "allowed_providers": sorted(current.allowed_providers),
                            "max_calls_per_task": current.max_calls_per_task,
                            "timeout_seconds": current.timeout_seconds,
                        }
                    )
                    result = {**serialise(request), "applied": True, "policy": str(save_subscription_bridge_policy(state_root, policy)), "enabled": policy.enabled}
                elif request.intent == "review_checklist":
                    if args.project_root is None or args.output is None:
                        raise ValueError("Natural review requires --project-root and a new --output path.")
                    manifest = build_manifest(args.project_root)
                    write_new_manifest(manifest, args.output)
                    result = {**serialise(request), "applied": True, "report": str(args.output.expanduser().resolve(strict=False))}
                elif request.intent == "skill_manual":
                    if args.output is None:
                        raise ValueError("Natural skill manual requires a new --output path.")
                    SkillRegistry(root / "config" / "skills.json").write_manual(args.output.expanduser().resolve(strict=False), include_discovered=True)
                    result = {**serialise(request), "applied": True, "manual": str(args.output.expanduser().resolve(strict=False))}
                elif request.intent == "skill_catalog":
                    result = {**serialise(request), "applied": True, "skills": [record.__dict__ for record in SkillRegistry(root / "config" / "skills.json").catalog(include_discovered=True)]}
                elif request.intent == "skill_search":
                    if not args.query or not args.query.strip():
                        raise ValueError("Natural skill search requires --query.")
                    result = {**serialise(request), "applied": True, "skills": [record.__dict__ for record in SkillRegistry(root / "config" / "skills.json").search(args.query, include_discovered=True)]}
                elif request.intent == "memory_extract":
                    if not args.task_id or not args.scope or not args.message:
                        raise ValueError("Natural memory extraction requires --task-id, --scope, and at least one --message.")
                    configuration = load_configuration(root)
                    state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
                    tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
                    decision, execution, result_candidates = extract_routed_subscription_candidates(
                        TaskLedger(root / tasks_directory),
                        ProviderRouter(root / state_directory / "provider-health.json"),
                        subscription_memory_adapters(),
                        requested_provider=None if args.provider == "auto" else args.provider,
                        trigger="explicit-request",
                        scope=args.scope,
                        task_id=args.task_id,
                        evidence="owner explicit natural-language memory request",
                        messages=[{"role": "user", "content": message} for message in args.message],
                        candidate_store=MemoryCandidateStore(root / state_directory / "memory-candidates.json"),
                        working_directory=root,
                    )
                    result = {**serialise(request), "applied": True, "provider": execution.provider, "fallback": decision.fallback, "candidates": result_candidates}
                elif request.intent == "provider_switch":
                    if not args.task_id or args.provider == "auto" or not args.instruction or not args.instruction.strip():
                        raise ValueError("Natural orchestrator switch requires --task-id, an explicit --provider, and --instruction.")
                    if not 1 <= args.provider_timeout_seconds <= 120:
                        raise ValueError("Provider timeout must be between 1 and 120 seconds.")
                    if args.switch_id is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.switch_id):
                        raise ValueError("Switch ID must be 1-128 letters, digits, dots, underscores or hyphens.")
                    channel = "orchestrator-switch"
                    if args.switch_id is not None:
                        channel = "switch-" + hashlib.sha256(args.switch_id.encode("utf-8")).hexdigest()[:56]
                    configuration = load_configuration(root)
                    state_directory = next(value for value in configuration.runtime_directories if value == "runtime/state")
                    tasks_directory = next(value for value in configuration.runtime_directories if value == "runtime/tasks")
                    ledger = TaskLedger(root / tasks_directory)
                    prompt = args.instruction.strip()
                    dispatch = ProviderGateway(
                        ledger,
                        ProviderRouter(root / state_directory / "provider-health.json"),
                        {"codex": CodexAdapter(), "claude": claude_adapter(), "antigravity": antigravity_adapter()},
                    ).dispatch(
                        task_id=args.task_id,
                        channel=channel,
                        maximum=1,
                        prompt=prompt,
                        working_directory=root,
                        requested_provider=args.provider,
                        timeout_seconds=args.provider_timeout_seconds,
                        allow_handoff=not args.strict_provider,
                        record_shared_handoff=args.shared_handoff,
                    )
                    decision, execution = dispatch.route, dispatch.result
                    result = {**serialise(request), "applied": True, "provider": execution.provider, "fallback": decision.fallback, "execution": execution.serialise()}
                else:
                    raise PermissionError("This natural-language request needs additional inputs or owner confirmation.")
            else:
                result = {**serialise(request), "applied": False}
        except (ConfigurationError, OSError, PermissionError, RuntimeError, ValueError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "skill":
        try:
            root = args.hub_root.expanduser().resolve(strict=True)
            load_configuration(root)
            registry = SkillRegistry(root / "config" / "skills.json")
            if args.skill_action == "list":
                result: object = [record.__dict__ for record in registry.catalog(args.all_installed)]
            elif args.skill_action == "catalog":
                result = [record.__dict__ for record in registry.catalog(include_discovered=True)]
            elif args.skill_action == "search":
                result = [record.__dict__ for record in registry.search(args.query, include_discovered=args.all_installed)]
            elif args.skill_action == "manual":
                registry.write_manual(args.output.expanduser().resolve(strict=False), include_discovered=args.all_installed)
                result = {"manual": str(args.output.expanduser().resolve(strict=False))}
            elif args.skill_action == "register":
                providers: dict[str, str] = {}
                for entry in args.provider:
                    name, separator, state = entry.partition("=")
                    if not separator or not name or not state:
                        raise ValueError("Provider must use NAME=STATE format.")
                    providers[name] = state
                result = registry.register(args.source, args.scope, args.kind, args.cost_tier, providers).__dict__
            else:
                raise AssertionError("unreachable skill parser state")
        except (ConfigurationError, OSError, ValueError, FileExistsError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    raise AssertionError("unreachable parser state")


if __name__ == "__main__":
    raise SystemExit(main())
