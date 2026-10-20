"""Small Korean-first, deterministic intent layer for owner-facing Hub actions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class NaturalRequest:
    intent: str
    mode: str
    safe_to_execute: bool
    required: tuple[str, ...]
    next_command: str
    explanation: str


def interpret(text: str, mode: str = "basic") -> NaturalRequest:
    """Translate a small safe vocabulary; ambiguity returns guidance, not action."""

    if mode not in {"basic", "operations", "development"}:
        raise ValueError("Mode must be basic, operations, or development.")
    normalized = " ".join(text.casefold().split())
    if not normalized:
        raise ValueError("Natural-language request must not be empty.")
    if "점검 리스트" in normalized or "코드 점검" in normalized:
        return _request("review_checklist", mode, False, ("project_root", "new_output_path"), "agent-hub review checklist --project-root PROJECT --output NEW_REPORT.json", "읽기 전용 점검 목록을 만들 준비가 됐습니다. 프로젝트와 새 보고서 경로가 필요합니다.")
    if "스킬 목록" in normalized or "스킬 카탈로그" in normalized:
        return _request("skill_catalog", mode, True, (), "agent-hub skill catalog", "등록·설치된 전체 스킬 카탈로그를 읽기 전용으로 보여드립니다.")
    if "스킬 설명서" in normalized:
        return _request("skill_manual", mode, False, ("new_output_path",), "agent-hub skill manual --output NEW_SKILL_MANUAL.md --all-installed", "통합 스킬 설명서를 만들 준비가 됐습니다. 새 출력 경로가 필요합니다.")
    if "스킬 검색" in normalized:
        return _request("skill_search", mode, False, ("query",), "agent-hub skill search --query KEYWORDS --all-installed", "검색할 스킬 키워드가 필요합니다.")
    if any(word in normalized for word in ("구독형", "subscription")) and any(word in normalized for word in ("langchain", "연동", "브리지")):
        if any(word in normalized for word in ("꺼", "중지", "비활성")):
            return _request("subscription_bridge_disable", mode, True, (), "agent-hub subscription-chat --hub-root HUB set --enabled false", "구독형 CLI–LangChain 브리지를 비활성화합니다.")
        if any(word in normalized for word in ("켜", "활성", "시작")):
            return _request("subscription_bridge_enable", mode, True, (), "agent-hub subscription-chat --hub-root HUB set --enabled true", "구독형 CLI–LangChain 브리지를 활성화합니다. 도구 호출은 계속 금지됩니다.")
    if "자가개선" in normalized and any(word in normalized for word in ("켜", "활성", "시작")):
        return _request("improvement_enable", mode, True, (), "agent-hub improvement schedule enable --every-minutes 10", "자가개선 observer를 10분 간격으로 활성화합니다.")
    if "자가개선" in normalized and any(word in normalized for word in ("꺼", "중지", "비활성")):
        return _request("improvement_disable", mode, True, (), "agent-hub improvement schedule disable", "Hub가 만든 observer scheduler 항목만 비활성화합니다.")
    if any(word in normalized for word in ("기억해", "기억 저장", "기억 추출")):
        return _request("memory_extract", mode, False, ("task_id", "scope", "message"), "agent-hub natural --text '기억해줘' --apply --task-id TASK --scope personal --message 'SAFE_TEXT'", "명시 요청 기억 추출로 해석했습니다. 범위, 작업 ID, 그리고 적재할 안전한 메시지가 필요합니다.")
    if "오케스트레이터" in normalized and any(word in normalized for word in ("클로드", "코덱스", "codex", "안티", "antigravity")):
        return _request("provider_switch", mode, False, ("task_id", "provider", "instruction"), "agent-hub natural --text '클로드를 오케스트레이터로' --apply --task-id TASK --provider claude --instruction 'CONTINUE_FROM_LEDGER'", "공급자 역할 전환으로 해석했습니다. task ID, 대상 provider, 그리고 원장 기준의 명시적 계속 작업 지시가 필요합니다.")
    return _request("guidance", mode, False, (), "agent-hub --help", "요청을 안전하게 특정할 수 없습니다. 기억 추출, 점검 리스트, 자가개선 켜기/끄기, 공급자 전환 중 하나로 표현해 주세요.")


def serialise(request: NaturalRequest) -> dict[str, object]:
    return asdict(request)


def execute_safe(request: NaturalRequest, enable_observer: Callable[[], object], disable_observer: Callable[[], object]) -> dict[str, object]:
    """Execute only the two explicit, reversible scheduler intents."""

    if not request.safe_to_execute:
        raise PermissionError("This natural-language request needs additional inputs or owner confirmation.")
    if request.intent == "improvement_enable":
        enable_observer()
    elif request.intent == "improvement_disable":
        disable_observer()
    else:
        raise PermissionError("Natural-language intent is not executable.")
    return {**serialise(request), "applied": True}


def _request(intent: str, mode: str, safe_to_execute: bool, required: tuple[str, ...], next_command: str, explanation: str) -> NaturalRequest:
    return NaturalRequest(intent, mode, safe_to_execute, required, next_command, explanation)
