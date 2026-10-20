# 구독형 CLI–LangChain 브리지 구현 계획

## 목적과 비목적

이 확장은 로그인된 Codex·Claude·Antigravity 구독 CLI를, **제한된 텍스트
응답 어댑터**로 LangChain/DeepAgents 흐름에 연결할 수 있게 한다. API 키나
구독 계정을 LangChain API credential로 가장하지 않는다. 네이티브 도구 호출,
스트리밍, 공급자 대화 상태 이전, 숨은 대화 이력 이전은 범위 밖이다.

기존 `ProviderRouter → TaskLedger → provider adapter`가 단 하나의 실제
실행 경로다. 브리지는 이 경로를 우회해 subprocess를 직접 실행하지 않는다.

## 변경 기준선과 나노 단위

| 단위 | 관찰 가능한 결과 | 완료 조건 | 실패·복구 |
|---|---|---|---|
| C0 | 정책 계약 | 기본 비활성, allowlist, 호출·시간 상한, 도구 금지 시험 통과 | 설정을 삭제/비활성화하면 실행 불가 |
| C1 | 의존성·공급자 정규화 | `langchain-core` 직접 고정, 세 CLI의 text 응답 fixture 통과 | lock/정규화 실패 시 의존성 변경을 되돌리고 C0만 유지 |
| C2 | 단일 실행 서비스 | ledger lease·router·redaction을 재사용하는 서비스 시험 통과 | 결과/lease를 보존하고 재시도하지 않음 |
| C3 | LangChain chat facade | `BaseChatModel`의 invoke/단일 응답 계약과 실패 변환 시험 통과 | 기본 비활성 상태 유지 |
| C4 | DeepAgents 경계·승인 연결 | DeepAgents의 `bind_tools` 요구를 명시 거절하고, approval 후 별도 text-only LangGraph 흐름만 실행 | 도구 요구 그래프는 실행 전 거절 |
| C5 | 재시작·한도·수용 시험 | 중복 호출 없이 재개, 제한 시 handoff, 실제 승인된 CLI 호출 통과 | task receipt로 차단하고 수동 재개 |
| C6 | 깨끗한 환경 이식 시험 | 설치→실행→검증→복구가 대상 환경에서 재현 | 미검증 플랫폼은 조건부로 유지 |

진행률은 위 단위를 **검증 완료한 비중**만 계산한다. C0 완료 전에는 어떤
CLI도 이 확장을 통해 호출하지 않는다.

## 현재 진행률

**C0·C6 완료 (7/7, 100%)** — 이 확장의 모든 합의된 Linux/WSL2 나노 단위를
검증했다. 정책·정규화·단일 실행 서비스·text-only `BaseChatModel` facade·승인
래퍼·영속 dispatch 영수증·실제 구독 CLI 수용시험·깨끗한 설치/복구 시험을
통과했다. 이 수치는 구독형 CLI–LangChain 브리지 확장에만 해당한다.

완료는 API 대체나 모든 플랫폼 지원을 뜻하지 않는다. 네이티브 Windows의
설치·실행·복구는 검증했지만, API 모델 기반 DeepAgents와 구독형 text-only
경로는 의도적으로 분리돼 있다. macOS는 아직 미검증이다.

## C0 정책 계약

`SubscriptionBridgePolicy`는 `schema_version`, `enabled`,
`allowed_providers`, `max_calls_per_task`, `timeout_seconds`, `allow_tools`만
받는다. 기본값은 `enabled=false`, 세 공급자 allowlist, task당 1회,
120초, `allow_tools=false`다. 알려지지 않은 필드와 credential 필드는
거절한다. 이것은 설정을 위한 계약일 뿐, 아직 실행 기능이나 설정 파일
자동 변경을 만들지 않는다.

## 환경·이식 판정

WSL2/Linux Python 3.12.15와 네이티브 Windows 11 CPython 3.12.10에서
`langchain-core 1.6.10` 및 선택 extra 설치·전체 검증·복구 설치를 재현했다.
로그인된 세 CLI의 실제 호출 근거는 WSL2/Linux 환경에 한정한다. 개인 경로·인증·토큰은
계약·로그·lock에 넣지 않는다.

text-only facade의 최소 설치는 `agent-hub[subscription-langchain]`이다.
LangGraph 승인 래퍼(C4)는 별도 `agent-hub[workflow]`도 필요하며, 없는
환경에서는 승인 래퍼 생성 시 명확히 거절한다.

## Owner 운영 제어

기본 상태는 비활성이다. owner만 Hub 런타임 상태 파일에서 다음 명령으로
명시적으로 설정을 바꿀 수 있다. 이 파일은 Git·provider 전역 설정·인증정보와
분리되며, tool calling은 어떤 설정에서도 활성화할 수 없다.

```bash
agent-hub subscription-chat --hub-root HUB status
agent-hub subscription-chat --hub-root HUB set --enabled true --provider claude --max-calls-per-task 1
agent-hub subscription-chat --hub-root HUB set --enabled false

# 자연어 제어 (명시적 --apply 필요)
agent-hub natural --hub-root HUB --text '구독형 LangChain 연동 켜줘' --apply
agent-hub natural --hub-root HUB --text '구독형 브리지 비활성화해줘' --apply
```

## 수용·이식 검증 근거

- 2026-10-15: 로그인된 Antigravity CLI를 단일 provider allowlist로 실제
  호출했다. 비어 있지 않은 정규화 응답, `succeeded` 실행 기록, lease 해제,
  dispatch 영수증 1개, 원장 내 진단 미저장을 확인했다.
- 2026-10-15: 새 `/tmp` Linux 가상환경에 wheel과 `langchain-core==1.6.10`을
  offline 설치했다. 기본 비활성 정책의 dispatch 차단과 의존성 검사를 통과했다.
  같은 환경에서 `agent-hub`를 제거하고 같은 wheel로 재설치하여 복구 후 동일
  차단 정책과 의존성 검사를 다시 통과했다.
- 2026-10-15: Windows 11/CPython 3.12.10의 독립 복사본에서 선택 extra 설치,
  109개 시험, `pip check`, 제거 후 재설치와 재검증을 통과했다. ChatGPT 로그인
  Codex CLI도 Hub 원장·라우터·read-only adapter 경로에서 실제 응답·실행 영수증·lease
  해제까지 통과했다. Windows Claude·Antigravity는 설치·로그인 상태가 없으므로
  그 OS에서의 실호출은 주장하지 않는다.
- macOS, provider CLI 미로그인 환경, 네트워크 단절 중 설치 단계는 지원한다고
  주장하지 않는다.
