# 환경 호환성 매트릭스

이 문서는 현재 관찰된 로컬 환경과 최종 목표 환경을 구분한다. `미설치` 또는 `미검증`은 실패가 아니라 아직 지원을 주장할 근거가 없다는 뜻이다.

| 구성요소 | 현재 상태 | 목표/최소 조건 | 판정 | 구현 원칙 |
|---|---|---|---|---|
| 운영체제 | WSL2 Linux + Windows 11 | WSL2 Linux 우선, Windows 직접 실행 | 지원 | WSL 정본은 `/home/sungt`에 두며 Windows에서는 독립 복사본으로 검증했다. |
| 파일시스템 | Linux ext 계열 + `/mnt/c` v9fs | Linux 경로를 상태 정본으로 사용 | 지원 | `/mnt/c`는 외부 원본 접근용이며 잠금 정본으로 쓰지 않는다. |
| Codex CLI | WSL2 `0.162.1`, Windows `0.162.0-alpha.17.2` ChatGPT 로그인 | 어댑터 적합성·실호출 시험 통과 | 지원 | Windows에서도 원장→라우터→read-only CLI→영수증·lease 해제를 확인했다. |
| Claude CLI | `2.1.300`, Claude.ai Pro 로그인 | 실제 plan 모드 계약 시험 통과 | 지원 | JSON 응답과 비대화형 읽기 전용 호출을 확인했다. |
| Antigravity CLI/API | `1.3.5`, Google 로그인 | 실제 sandbox·plan 모드 계약 시험 통과 | 지원 | 실제 바이너리 별칭은 `agy`다. |
| Python | `3.10.12` + 격리 `3.12.15` | 하네스 전용 `3.12` | 지원 | 기존 Python은 보존하고 하네스는 격리 환경을 사용한다. |
| Node.js | `22.23.3` 설치 | 공급자 도구별 확인 | 조건부 | 현재는 하네스 필수 의존성으로 가정하지 않는다. |
| Git | `2.34.1` 설치 | 버전 관리·복구 시험 | 준비됨 | 코드·정책만 추적하고 런타임 데이터는 제외한다. |
| bubblewrap | `0.6.1` 설치, user/network namespace 동작 확인 | 운영체제 격리 개선 테스트 | 조건부 | 자가개선 후보 테스트 실행에 필요하며, 미설치 환경에서는 자동 승격을 중단한다. |
| LangGraph | `1.2.15` + SQLite checkpoint `3.1.2` | 잠금된 Python 의존성 + 시험 | 지원 | 승인·재개 최소 흐름을 시험했다. |
| LangMem | `0.0.30` 설치 | 잠금 파일 + 범위 시험 | 부분 지원 | 후보 기억으로만 제한하고 하네스 권한 체계를 우회하지 않는다. |
| DeepAgents | `0.7.25` 설치 | Python `>=3.11` + 권한·스킬 시험 | 부분 지원 | 하네스 권한 체계를 대체하지 않는다. |
| 구독형 CLI–LangChain facade | `langchain-core 1.6.10` + 세 CLI adapter | WSL2/Linux clean install·owner 로그인 | 조건부 지원 | text-only, 기본 비활성, API 모델·DeepAgents 도구 경로와 분리한다. |
| 네이티브 Windows | Windows 11, CPython 3.12.10 + 선택 extra | 설치·실행·복구 수용시험 | 지원 | 사용자 범위 CPython으로 설치·전체 시험·제거 후 재설치를 실제 수행했다. POSIX 권한·symlink 격리 시험은 OS 권한에 따라 skip된다. |
| macOS | CPython 3.12 + 선택 extra | 설치·실행·복구 수용시험 | 미검증 | 현 로컬에 macOS 호스트가 없다. CI matrix로 재현 경로 제공. |
| Loadout/Knot 도구 | 미설치 | 중앙 Registry/컴파일러 계약 | 미검증 | 수동 설치물에 의존하지 않고 하네스 계약부터 만든다. |
| SQLite CLI | 미설치 | Python 표준 `sqlite3` 모듈로 시작 | 대체 가능 | CLI는 선택 사항이다. |
| Docker/uv/pipx | 미설치 | 필수 아님 | 대체 가능 | 재현성에 실제 필요할 때만 도입한다. |

## 설치 이력

- 2026-10-15: Ubuntu 패키지 `python3-pip`, `python3-venv`를 설치해 하네스 내부의 격리 부트스트랩 환경을 만들 수 있게 했다. 이 단계는 Codex·Claude·Antigravity 설정을 변경하지 않는다.
- 2026-10-15: Claude Code `2.1.300`과 Antigravity CLI `1.3.5`를 사용자 전용 `~/.local`에 설치하고 로그인·실제 읽기 전용 호출을 검증했다. Antigravity 설치기는 `~/.bashrc`와 `~/.profile`에 `~/.local/bin` PATH 행을 추가했다.
- 2026-10-15: Windows 사용자 범위에 공식 `Python.Python.3.12` 3.12.10을 설치했다. 임시 독립 작업 복사본에서 선택 extra 설치, `compileall`, 109개 단위 시험, `pip check`, 패키지 제거 후 재설치와 동일 검사를 통과했다.
- 2026-10-15: Windows의 ChatGPT 로그인 Codex CLI `0.162.0-alpha.17.2`로 신뢰 Git 작업공간에서 실제 read-only 요청을 수행했다. Hub의 task ledger·router·Codex adapter를 거쳐 실행 성공 기록 1건, 비어 있지 않은 응답, lease 해제를 확인했다.
- `uv`와 Python 3.12는 저장소 내부의 `.bootstrap-venv`와 `.venv`에서만 사용한다. DeepAgents의 공식 배포 조건에 맞춰 전체 하네스 지원 하한은 Python 3.11이며, 시스템 Python 3.10은 부트스트랩용으로만 보존한다.

## 이식성 판정 기준

- 지원: 깨끗한 대상 환경에서 설치·실행·검증·복구를 재현했다.
- 조건부 지원: 필요한 외부 도구·권한·버전이 명시되어 있고 검증됐다.
- 미검증: 계획 또는 설치 가능성만 있으며 실행 증거가 없다.
- 미지원: 의도적으로 범위 밖으로 선언했다.

공급자, Python, 파일시스템, 경로 인코딩, 절전·재시작, 네트워크 단절, 권한 거부는 통합 시험 항목으로 유지한다.

## 대상 OS 수용시험 실행법

각 OS에서 새 가상환경을 만든 뒤 다음을 실행한다. 이 검사는 provider CLI를
호출하거나 Hub runtime 설정을 바꾸지 않는다.

```bash
python -m pip install ".[workflow,memory,agents,subscription-langchain]"
python scripts/verify_portability.py
```

GitHub Actions를 사용하는 저장소에서는 `.github/workflows/portability.yml`이
Ubuntu·Windows·macOS에서 같은 검사를 수행한다. Windows는 위 로컬 직접
수용시험으로 지원 근거가 있다. macOS는 이 워크플로가 실제로 실행돼 성공하기
전까지 미검증으로 유지한다.
