# 다음 작업 이어하기

이 문서는 새 대화나 작업 세션에서 구현을 바로 이어가기 위한 기준점이다. 코드 상태와 게이트별 사실은 `IMPLEMENTATION_STATUS.md`, 요구사항 정본은 `IMPLEMENTATION_PLAN.md`를 따른다.

## 2026-10-20 세션 저장점 — Loadout·Hub 브리지 경화 완료

이 절이 현시점의 최우선 정본이다. 아래의 오래된 진행 서술과 충돌하면 이 절을 따른다.

- 구조 경계: `/home/sungt/agent-hub`는 통합·제어 계층이며, 공식 Loadout은
  `/home/sungt/loadout-workspace`에 분리 설치돼 있다. Loadout 스캐폴드를 Hub에
  덮어쓰지 않는다. Hub 자체의 어댑터·통합 코드 개선은 허용된다.
- Hub 지침 통합: 스캐폴드가 아닌 공식 조각 `agent-loop`·`knot`·`guard`·`no-yesman`은
  2026-10-20에 Hub 루트의 `CLAUDE.md`와 `AGENTS.md`에 Store 관리 블록으로
  설치됐다. 따라서 새 Claude·Codex 작업은 Hub 트리에서 이 네 지침을 읽을 수
  있다. `multiagent`는 기존 파일을 대량 생성하는 스캐폴드이므로 Hub에 복사하지
  않으며, 아래의 task bridge를 통해 분리된 Loadout 작업공간과 연결한다. guard의
  전역 런타임 스위치는 여전히 `off`다.
- 로드 범위 검증: 같은 날 새 Codex·Claude CLI 세션을 각각 무도구 읽기 전용으로
  Hub 루트와 `src/agent_hub`에서 시작해 네 일반 조각을 모두 인식하는지 확인했다.
  이는 새 세션의 프로젝트 지침 발견만 증명하며, 이미 열린 세션의 재로딩이나
  Claude↔Codex 자동 전환은 증명하지 않는다.
- 외부 호출 정본: Hub `TaskLedger`가 외부 worker 영수증의 정본이다. Loadout의
  `call_worker.sh`(Codex·Antigravity)와 `native_worker_receipt.sh`(native Claude)는
  호출 전 `reserve-external`, 종료 뒤 `complete-external`을 기록하고 이어서
  `loadout sync-task`로 `task.md`의 관리 블록을 갱신한다. 미바인딩 task, 중복
  fingerprint, 완료 task의 새 호출은 fail-closed로 거부된다.
- legacy Loadout task migration: `agent-hub loadout migrate-tasks --project-root
  /home/sungt/loadout-workspace --scope personal --apply`로 기존 5개 task를 단방향
  바인딩했다. `loadout-hardening` Hub task는 완료됐고, 나머지 review cycle task는
  기존 Loadout 상태를 보존한 채 활성 상태다. migration은 누락된 ID만 첫 YAML fence에
  넣으며, 재바인딩과 `tasks/` 밖 symlink 탈출을 거부한다.
- Loadout에서 native Claude Task 호출은 기계적으로 감싼 subprocess가 아니라
  `CLAUDE.md`의 start/complete 절차를 따른다. 따라서 이 경계는 정적 호환성 검사와
  운영 지침으로 강제된다. 직접 Task 도구 호출을 자동 가로채는 런타임 훅은 없다.
- 실제 검토 영수증: Claude는 브리지 구현 파일을 읽고 PASS했다. Antigravity는
  헤드리스 `read_file` 권한이 자동 거부되므로, 제공된 설계 사실만으로 한 무도구
  독립 검토가 PASS했다. 권한 우회나 `--dangerously-skip-permissions`는 사용하지 않았다.
- Knot: 포인터 `~/.config/knot/vault`는 `/home/sungt/knot-vault`를 가리킨다.
  `python3 /home/sungt/knot-vault/scripts/lint.py /home/sungt/knot-vault`는
  `errors=0`이다. Loadout 호환성 검사는 이제 pointer/env vault에 명시 경로 lint까지
  요구한다. Hub KnowledgeStore와 Knot 사이에는 의도적으로 자동 양방향 동기화가 없다.
  scope·소유권·불변 raw 규약을 침해하지 않는 승인형 매핑 설계 전에는 자동 복사를 추가하지
  않는다.
- Guard: codexbar는 WSL에서는 musl 정적 빌드가 필요했고 실제 상태 조회 및 coach
  검증은 과거에 통과했다. 그러나 현재 guard는 `off` 상태이며 호환성 검사가 이를 요구한다.
- 최신 검증: Agent Hub `unittest discover -s tests` 168 PASS; Loadout
  `_shared/check-invariants.sh` 16 PASS; `scripts/verify-loadout-compat.sh` PASS;
  Hub `git diff --check` PASS. Loadout 작업공간은 Git 저장소가 아니므로 Git diff 검사는
  적용하지 않는다.

다음 세션의 안전한 첫 명령:

```bash
cd /home/sungt/agent-hub
.venv/bin/python -m unittest discover -s tests
bash /home/sungt/loadout-workspace/_shared/check-invariants.sh
bash /home/sungt/loadout-workspace/scripts/verify-loadout-compat.sh
```

## 현재 기준점

- 저장소: `/home/sungt/agent-hub` (WSL2 Linux ext 파일시스템)
- Python: 하네스 `.venv`의 3.12.15, 시스템 Python 3.10은 부트스트랩 용도
- 의존성: `uv.lock`이 있으며 LangGraph/SQLite checkpoint, LangMem, DeepAgents 버전이 고정됨
- 공급자: Codex CLI 0.162.1, Claude Code 2.1.300, Antigravity CLI 1.3.5 (`agy` 실행 파일)
- Claude 및 Antigravity 로그인과 각각의 안전 모드 비대화형 응답 시험이 완료됨
- 같은 원장 작업에서 Codex→Claude→Antigravity 실제 실행을 완료했고, 각 실행의 provider 기록과 lease 해제를 확인했다.
- 최초 구현 계획 Phase 0~12 수용 게이트 완료. 가장 최근 회귀 시험 기록: Linux/WSL2와 Windows 11에서 각각 112개 통과 (`compileall`, `pip check`, staged diff 검사도 통과). 기본 설치 전용 가상환경의 CLI 기동도 통과했다.
- 전수 점검 매니페스트 최근 결과: 포함 76, 제외 10, 보류 0 (`runtime/reports/review-remediation-final-20261015.json`). 최종 통합 코드 리뷰 결과는 `FINAL_INTEGRATION_REVIEW.md`에 기록했다.
- Git: 구현 파일과 문서가 stage되어 있음. 작성자 이름/이메일이 설정되지 않아 커밋은 아직 없음. 자동으로 가짜 작성자 정보를 설정하지 않는다.
- 개인 스킬 경로가 든 `config/skills.json`, Python 환경, 런타임 파일, CLI 인증 정보는 저장소 커밋 대상에서 제외한다. `config/skills.example.json`은 이식 가능한 빈 템플릿이다.
- Antigravity 설치기가 `~/.bashrc`와 `~/.profile`에 `~/.local/bin` PATH 행을 추가한 사실은 `COMPATIBILITY_MATRIX.md`에 기록돼 있다.

## 바로 이어서 할 작업

### 나노 단위 A — 실제 원장 기반 공급자 전환

완료: `orchestration.run_routed_task`에 세 provider 어댑터를 연결하고 실제 동일 작업 원장에서 지정 전환을 확인했다. 실행 상태는 runtime 작업 `Three-provider live handoff verification`의 events/snapshot에 기록됐다.

나노 단위 B부터 E까지는 완료됐다. 자연어의 구독형 후보 추출과 오케스트레이터 전환도 task ID·입력 검증·원장 라우터 수용 시험까지 완료했고, Antigravity 구독 CLI 실제 후보 추출도 통과했다. DeepAgents는 SQLite 승인 interrupt·프로세스 재생성 뒤 승인된 read-only graph 실행까지 통과했다. 이후 작업은 최초 구현 계획 밖의 선택적 운영 확장이다.

### 나노 단위 B — LangMem 후보 격리

LangMem의 extractor/manager 결과를 곧바로 `KnowledgeStore`에 쓰지 않는다. 후보 저장소를 별도로 두고 `scope`, 출처 작업 ID, 근거, 중복 키, 상태(`candidate/approved/rejected`), 시각을 기록한다. owner 승인 전 운영 검색 결과에서 후보가 보이지 않는지 확인한다. LangMem 삭제 기능은 계속 비활성화하며, 삭제·보존의 정본은 KnowledgeStore tombstone이다. 개인/회사 범위 승격은 소유자의 명시 동작으로만 수행한다.

통과 기준: fake-model 시험에서 후보 추출·중복 제거·승인·거절·재시작 후 상태 유지가 동작하고, 승인 전 검색 격리 및 범위 간 접근 거부가 확인된다. 실제 모델 호출은 별도 의존성/비용 확인 없이 시험에 포함하지 않는다.

완료: 별도 candidate 저장소, 범위별 접근, 중복 제거, owner CLI의 list/promote/reject, live KnowledgeStore와 사전 격리.

### 나노 단위 C — DeepAgents 권한 경계

완료: approved same-scope registry 스킬 및 활성 knowledge ID로만 graph 생성, 메인 도구에서 write/edit/delete/execute 제거, 하위 에이전트에도 쓰기 거부 규칙, FilesystemBackend 실행 기능 없음. 코드·범위·도구 구성 테스트 통과.

### 나노 단위 D — 자가개선 적용·롤백

구현됨: bubblewrap이 설치된 환경에서 네트워크 namespace와 홈 경로를 격리한 복사본으로 baseline/candidate 테스트를 실행하고, 승인된 저위험 소스만 원자적으로 적용한다. 증거 해시·보호 파일·일일 상한·실패 4시간 cooldown·백업 rollback을 검증했다. observer는 하나의 후보만 처리하며, WSL user systemd bus가 없는 현재 환경에서는 관리형 사용자 crontab이 10분마다 실행한다. `improvement schedule disable`로 Hub 태그 행만 제거할 수 있다.

### 나노 단위 E — 복구 및 최종 전수 리뷰

완료한 조각: 백업 verify 및 restore 미리보기/명시적 overlay 적용, 적용 직전 안전 스냅샷 생성, 적용 중 오류 시 메모리 내 이전 바이트 복구. 악성 경로·손상 archive·symlink 백업 경로 거부, CLI 수명주기를 시험했다. 복원은 아카이브에 포함된 허용 경로 파일만 덮어쓰거나 추가하며, 아카이브에 없는 파일은 삭제하지 않는다. 최신 체크리스트는 포함 55·제외 9·보류 0이지만 포함 소스의 실제 코드 리뷰는 아직 남아 있다.

사용량 제한은 현재 요청에도 자동 handoff한다. 제한된 공급자의 결과와 lease를 원장에 먼저 남기고, 같은 task ID에서 다음 가용 공급자로 재획득한다. 대체 공급자도 제한되면 그 결과를 보존하고 종료한다. 제한이 아닌 일반 실패는 자동 재시도하지 않아 반복 실행 위험을 줄인다.

재시작 복구는 lease 자동 탈취가 아니라 owner의 `task lease-recover TASK_ID --stale-after-seconds N` 동작이다. 유예시간 전에는 거부되고, 이후에는 append-only `lease_recovered`를 기록한 뒤 후속 provider가 같은 task ID를 이어받는다. provider child process는 다른 CLI의 명시적 profile override 환경변수를 제거한다. 인증정보는 Hub에 복사하지 않으며 각 CLI의 기존 로그인 상태를 사용한다.

네트워크 unavailable·사용량 제한은 해당 실행을 원장에 남기고 다음 가용 provider로 handoff한다. 모든 provider가 해당 상태면 마지막 결과를 보존한다. Phase 12의 계약 검증과 전체 코드 리뷰는 완료됐다.

`compileall`, `unittest` 112개, 매니페스트 정합성, staged diff 공백 오류는 통과했다. Python 번들 `ensurepip`로 Hub `.venv`에 pip를 복구한 뒤 `python -m pip check`도 통과했다. Antigravity 구독 CLI의 실제 후보 추출은 `provider=antigravity`, fallback 없음, 후보 큐만 적재·lease 해제까지 검증됐고, DeepAgents approval guard는 restart-safe 승인 뒤에만 실제 read-only graph를 호출했다. Windows ChatGPT 로그인 Codex CLI도 Hub 원장·router·read-only adapter에서 실제 응답과 lease 해제를 확인했다. 선택 의존성은 기본 CLI에서 지연 import되고 Windows batch launcher는 보안상 거절된다. 의존성 잠금의 정본은 `uv.lock`이다.

## 다음 세션 첫 확인

1. `git status --short`와 `git diff --cached --stat`으로 저장된 기준점을 확인한다.
2. `PATH="/home/sungt/.local/bin:$PATH" .venv/bin/agent-hub environment validate --hub-root .`로 현재 공급자 감지를 확인한다.
3. API 모델을 쓸 경우에만 `AGENT_HUB_LANGMEM_MODEL`과 해당 공급자 API 인증을 설정한다. 구독형 CLI 기억 추출은 별도 API 키 없이 `memory-candidate extract-routed-cli` 또는 자연어 `--apply --task-id --scope --message`로 실행하되, 명시 요청마다 후보만 적재되는지 먼저 확인한다.
