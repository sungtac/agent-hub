# 구현 상태

진행률은 파일 생성이 아니라 각 나노 단위의 검증 게이트 통과 여부로 계산한다.

| Phase | 나노 단위 | 상태 | 검증 근거 |
|---|---|---|---|
| 0 | 0.1 읽기 전용 환경 검사 | 완료 | 단위 시험, JSON 출력, 경로 비식별화, 비덮어쓰기·리소스 정리 시험 통과 |
| 0 | 0.2 호환성 계약 검사 | 완료 | 단위 시험과 실제 Codex/Python/Claude/Antigravity 상태 분류 통과 |
| 0 | 환경 계약·호환성 매트릭스 | 완료 | `environment validate`와 `COMPATIBILITY_MATRIX.md`가 현재/목표 차이를 일치하게 분류 |
| 1 | 1.1 구성·런타임 경계 | 완료 | 구성 비덮어쓰기, 위험 경로 거부, `700` 경계, 재실행 시험 통과 |
| 1 | 1.2 초기화 격리·복구 설계 | 완료 | 외부 공급자 설정 불변 해시 시험과 복구 정본 설계 기록 완료 |
| 1 | 저장소 골격·런타임 경계 | 완료 | 새 초기화, 반복 초기화, 외부 설정 불변 조건 통과 |
| 2 | 2.1 작업 원장·승인·lease | 완료 | 이벤트 정본 재생, stale lease 거부, 동시 lease 경합, CLI 수명주기 시험 통과 |
| 2 | 2.2 중단·내구성 처리 | 완료 | 스냅샷 재생, 파일·디렉터리 동기화, 원자 스냅샷 교체 시험 통과 |
| 2 | 작업 원장·감사 이벤트 | 완료 | 생성·승인·리스 이벤트가 append-only 원장에 기록됨 |
| 3 | Codex 단일 공급자 어댑터 | 완료 | 실제 read-only 호출, 시간초과 종료, 결과 정규화, 원장 결합 시험 통과 |
| 4 | 읽기 전용 점검 리스트 | 완료 | 전수 매니페스트, 해시, 제외·보류 근거, 실제 하네스 실행 시험 통과 |
| 5 | LangGraph 승인·재개 | 완료 | SQLite 체크포인트, 승인 interrupt, 재시작 후 resume, 결정적 영수증 시험 통과 |
| 6 | Registry·중앙 Loadout | 완료 | 기존 스킬 비복사 등록, 메타데이터 검색·설명서·owner CLI, 공용 온오프, `orchestration-handoff`를 포함한 공급자별 프로젝트 전용 오버레이 시험 통과; 현재 `coding-principles`와 `document-analysis`가 personal registry에 등록됨 |
| 7 | 지식 범위·삭제 영수증 | 완료 | 범위 제한 색인·버전·승격·삭제 전파·해시 백업/검증/미리보기/명시적 overlay 복원 및 LangMem 후보 격리·owner 승인 CLI 시험 통과; Antigravity 구독 CLI 실제 후보 추출·원장·후보 격리 수용 시험 통과. LangChain API 모델은 선택적 별도 운영 경로 |
| 8 | 공급자 전환 라우팅 | 완료 | 동일 작업 원장에서 Codex→Claude→Antigravity 실호출 3회, 일반 작업·구독형 기억 추출·명시 instruction 자연어 오케스트레이터 전환에서 제한·네트워크 unavailable·timeout 시 대체 공급자로 lease handoff, 모두 제한된 경우 결과 보존 및 lease 해제 확인 |
| 9 | LangMem·DeepAgents 최소 연결 | 완료 | LangMem manager 출력 후보 격리·중복·owner 승인 CLI, 명시적 API 모델 extract 경로 및 Codex·Claude·Antigravity 구독 CLI 공통 후보 계약/원장 handoff 경로(명시 trigger·민감정보·품질 제한), Antigravity 실제 후보 추출·후보만 적재되는 운영 수용 시험, 범위별 registry 스킬/지식만 넣는 DeepAgents 읽기 전용 도구 구성, 실제 DeepAgents graph가 SQLite 승인 interrupt·프로세스 재생성·승인 뒤에만 실행되는 수용 시험 통과 |
| 10 | 자가개선 제어 하네스 | 완료 | 네트워크·홈 격리 bubblewrap 시험, 기준/후보 테스트, 안전 파일만 승격, 해시 백업·롤백, 실패 쿨다운·일일 제한·보호 영역 및 한 번에 한 후보를 처리하는 영속 observer/CLI 시험 통과; WSL user systemd bus 부재에 맞춰 관리형 cron scheduler를 10분 간격으로 활성화 |
| 11 | Antigravity 어댑터 | 완료 | 세 공급자 모두 실제 adapter→router→ledger 경로에서 지정 호출 성공, 결과 정규화·lease 해제 통과 |
| 12 | 운영 경화·최종 전수 리뷰 | 완료 | 백업 복원·사전 안전 스냅샷, 명시적 유예시간 lease 복구(절전 뒤에도 동일), 사용량 제한·네트워크 unavailable 시 handoff, 공급자 profile override 격리, 수동 통합 코드 리뷰 및 전체 파일 매니페스트(포함 58·제외 9·보류 0) 검증 |

## 현재 검증 사실

- Codex CLI는 현재 감지된다.
- Python 3.10은 부트스트랩 호환성 시험용으로 유지한다.
- Python 3.12.15 격리 환경과 LangGraph SQLite 체크포인트 의존성은 설치·시험됐다.
- Claude Code `2.1.300`과 Antigravity CLI `1.3.5`는 로그인 및 안전 모드 실제 호출까지 검증됐다.
- 이 상태 문서는 실행 상태가 아니라 검증 이력의 사람이 읽는 요약이다.

최초 구현 계획의 Phase 0~12 수용 게이트는 모두 통과했다. 가장 최근 회귀 시험은 Linux/WSL2와 Windows 11에서 각각 112개 통과이며, 양쪽에서 `compileall`과 `pip check`도 통과했다. base 설치 전용 가상환경에서 optional extra 없이 `environment`, `init`, 구독 브리지 정책 명령의 기동도 확인했고 같은 검사를 OS별 CI에 추가했다. Windows에서는 POSIX 권한, symlink 생성 권한, bubblewrap 및 POSIX fixture가 필요한 11개 시험이 의도적으로 skip된다. Antigravity `1.3.5` 구독 CLI는 실제 personal task에서 `provider=antigravity`, `fallback=false`, 실행 성공·lease 해제, 후보 큐만 적재, 지식그물 미승격까지 수용 검증됐다. DeepAgents read-only graph는 SQLite 승인 interrupt 뒤 프로세스를 재생성한 뒤에도 승인 후에만 실행됨을 검증했다. 구독형 CLI–LangChain text facade는 기본 비활성·owner runtime 온/오프·allowlist·task당 영속 dispatch 영수증·중복 resume 차단까지 구현했고, 실제 Antigravity 호출과 WSL2/Linux 및 Windows 설치/제거/복구 설치를 검증했다. 자연어 제어층은 `--apply`에서도 scheduler 변경·구독형 브리지 온/오프·충분한 입력을 받은 읽기 전용 점검·스킬 작업·명시 요청 기억 후보 추출·명시 instruction 오케스트레이터 전환만 실행한다. 중앙 Loadout의 `orchestration-handoff` 모듈은 세 provider 프로젝트 오버레이에 동일 원장 전환 지침을 렌더링하며 끌 수 있다. 현재 Hub에는 세 provider용 관리 오버레이가 생성돼 있고 `loadout verify` 결과도 모두 `current`다. 전환은 동일 task ID의 원장 handoff를 이용하며, 살아 있는 lease를 강탈하지 않는다. 다음 작업 시작점은 [`CONTINUATION.md`](CONTINUATION.md)이다. 외부 API 모델 설정 같은 선택 항목은 완료로 계산하지 않는다.
