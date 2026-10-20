# 최종 통합 코드 리뷰

검토 기준일: 2026-10-15. 이 보고서는 `점검 리스트`가 생성한 파일
목록과 별개로, Hub 소스의 설계·경계·오류 처리·중복을 수동 검토한
결과다. 포함 파일이 자동으로 “안전”하다는 뜻은 아니며, 아래의 미해결
항목은 지원 완료로 표시하지 않는다.

## 범위와 결과

| 영역 | 검토 파일 | 결과 | 확인한 경계 |
|---|---|---|---|
| 구성·환경 | `configuration`, `environment`, `compatibility` | 통과 | 런타임 경로 제한, 전역 공급자 설정 미변경, 읽기 전용 검사 |
| 작업·재개 | `ledger`, `workflow`, `orchestration`, `routing` | 통과 | append-only 원장, 세대 lease, 승인 checkpoint, 제한 handoff, 명시적 stale lease 복구 |
| 공급자 | `providers` | 조건부 통과 | shell 미사용 argv 실행, timeout·제한 분류, CLI 간 profile override 제거 |
| 구독형 LangChain | `subscription_langchain`, `ledger`, `workflow`, `cli`, `natural_language` | 조건부 통과 | 기본 비활성, owner runtime 온/오프, allowlist·dispatch 영수증, text-only facade, 중복 resume 차단, DeepAgents 도구 경로와 분리 |
| 지식·기억 | `knowledge`, `memory_candidates`, `integrations`, `subscription_memory` | 조건부 통과 | scope 분리, tombstone, 후보 격리·owner 승인, 구독형 CLI 공통 후보 계약, DeepAgents 읽기 전용 projection |
| 스킬·Loadout | `skills`, `loadout` | 통과 | 메타데이터 우선 검색, 상위 Hub 중심 온오프, 세 provider에 공통 원장 handoff 지침을 넣는 프로젝트 overlay만 생성 |
| 백업·개선 | `backup`, `improvement`, `sandbox`, `observer`, `scheduler` | 통과 | manifest 검증, 명시적 overlay 복원·안전 백업, bwrap 격리, 단일 후보 observer, 사용자 crontab scheduler |
| 점검·CLI | `review`, `cli` | 통과 | 비덮어쓰기 매니페스트, 명시적 destructive apply, owner 로컬 명령 경계 |

## 실제 검증

- `compileall`: 통과
- `unittest discover`: Linux/WSL2 112개 통과, Windows 11에서도 112개 통과
- `git diff --cached --check`: 통과
- 최신 점검 매니페스트: 포함 76개, 제외 10개, 보류 0개. 런타임 보고서는
  Git 추적 대상이 아니다.
- `pip check`: Python 번들 `ensurepip`로 Hub `.venv`에 pip 25.0.1을
  복구한 뒤 `No broken requirements found`를 확인했다.

## 해결한 충돌·중복 위험

1. provider의 내부 대화 상태는 이식하지 않고, 공통 task ledger만
   handoff 정본으로 사용한다.
2. LangMem은 운영 지식에 직접 쓰지 않고 후보 저장소를 거친다.
3. DeepAgents는 등록된 같은 범위 스킬·지식만 읽고, 쓰기·삭제·shell을
   주 도구에서 제공하지 않는다.
4. 자가개선은 observer가 한 번에 하나만 처리하고 sandbox·백업·일일
   상한·cooldown을 통과한 경우만 반영한다.
5. 복원은 명시적 `--apply`가 필요하며, 먼저 현재 상태를 안전 백업한다.
6. 구독형 CLI facade는 API credential이나 provider tool protocol로 가장하지
   않는다. 승인된 text-only 호출은 영속 dispatch 영수증을 먼저 남겨 재시작·중복
   UI resume가 외부 CLI를 중복 실행하지 못하게 한다.
7. 선택 의존성은 기본 CLI import 경로에서 분리했다. 구독 브리지 정책은
   dependency-free 모듈에 두고, LangGraph 승인 기능은 실제 생성 시점에만
   import한다. base 설치 전용 CI 수용시험도 추가했다.
8. Windows `.cmd`/`.bat` launcher는 `cmd.exe` 재해석으로 prompt를 명령으로
   바꿀 수 있어 지원하지 않는다. native executable만 허용하며, batch launcher는
   provider를 시작하기 전에 명시적으로 거절한다.
9. observer의 오래된 `running` 후보는 한 시간 뒤 `pending`으로 복구한다.
   정상 실행 중인 최신 후보를 중복 처리하지 않으면서 강제 종료 뒤 영구 정체를
   막는다.

## 독립 에이전트 코드리뷰 후속 조치

2026-10-15에 Claude와 Antigravity에게 독립적인 읽기 전용 리뷰를 요청했다.
두 결과가 공통으로 지적한 Windows batch launcher 명령 주입 위험을 수정했다.
Claude가 지적한 선택 의존성 import, base-install CI 공백, observer 고아 실행도
수정했고, 스킬 scope/손상 레지스트리 검증과 Windows scheduler 안내도 보완했다.
수정 뒤 Linux/WSL2와 Windows 11에서 각각 112개 단위 시험, `compileall`,
`pip check`를 통과했다. Antigravity의 일부 “파일 누락” 지적은 축소 임시 사본에
의도적으로 포함하지 않은 파일에 대한 오탐이어서 채택하지 않았다.

## 미해결·외부 검증 항목

1. LangMem의 실제 상용 모델 추출 경로는 `AGENT_HUB_LANGMEM_MODEL`과
   `memory-candidate extract`로 구현됐다. 별도로 Codex·Claude·Antigravity
   구독형 CLI도 `memory-candidate extract-routed-cli` 및 자연어 `--apply`
   경로에서 같은 후보 계약·원장 handoff를 사용하며, Antigravity 직접
   호출·후보 격리 수용 시험까지 통과했다. 구독형 로그인은
   LangChain API 모델 인증으로 자동 전환되지 않으므로, API 모델 경로를
   운영하려면 별도 인증·비용 정책이 필요하다.
2. provider CLI의 버전별 실제 명령 계약은 설치된 세 CLI에서 한 번씩
   재검증해야 한다. 현재 adapter conformance의 구조·실호출 이력은 있으나
   Windows에서는 ChatGPT 로그인 Codex CLI의 원장→라우터→read-only adapter
   실호출까지 확인했다. Claude·Antigravity는 Windows에 설치·로그인된 대상이
   없으므로 그 OS의 실호출은 보수적으로 `conditional`을 유지한다.
3. observer는 WSL의 user systemd bus 부재에 맞춰 사용자 crontab에서
   10분마다 `improvement run-once`를 호출한다. `schedule disable` 명령은
   Hub가 추가한 태그 행만 제거한다.
4. 백업은 허용 파일의 overlay 복원이다. 삭제 동기화나 외부/암호화 백업
   purge는 별도 보존 정책 구현이 필요하다.
5. 모든 선택 extra를 포함한 wheel의 깨끗한 WSL2/Linux 설치·초기화·제거·복구
   설치와 Windows 11/CPython 3.12.10의 설치·109개 시험·제거 후 재설치를
   검증했다. Windows에서는 POSIX 전용 권한·symlink·bubblewrap 격리 시험이
   환경 제약에 따라 skip되며, 관련 기능은 조용히 대체되거나 자동 승격을
   중단한다. macOS는 현 로컬에 호스트가 없어 아직 미검증이다.

따라서 최초 구현 계획의 하네스 코어 수용 게이트는 완료됐지만, 위 외부
운영 항목은 환경별로 재검증해야 하는 명시적인 조건부 항목이다.
