# Agent Hub

`agent-hub`는 Codex, Claude, Antigravity와 공용 스킬·Loadout·지식그물·자가개선을 연결할 독립 하네스의 관리 저장소다.

현재 상태는 **로컬 하네스 코어 구현 및 검증 단계**다. 실행 코드와
로컬 런타임 경계는 구현됐으며, 공급자 전역 설정·CLI 인증정보·사용자
지식·작업 로그·비밀값은 Git에 저장하지 않는다. 실제 상용 LangMem 모델
추출과 호스트 scheduler 등록은 별도의 조건부 운영 항목이다.

- 확정 기준선: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- 구현 전 점검: [docs/PREIMPLEMENTATION_READINESS.md](docs/PREIMPLEMENTATION_READINESS.md)
- 환경 호환성 매트릭스: [docs/COMPATIBILITY_MATRIX.md](docs/COMPATIBILITY_MATRIX.md)
- 나노 단위 실행 기준: [docs/NANO_EXECUTION_PLAN.md](docs/NANO_EXECUTION_PLAN.md)
- 검증 기반 구현 상태: [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
- 복구 설계 기준: [docs/RECOVERY_DESIGN.md](docs/RECOVERY_DESIGN.md)
- 의존성 출처: [docs/DEPENDENCY_PROVENANCE.md](docs/DEPENDENCY_PROVENANCE.md)
- 최종 통합 리뷰: [docs/FINAL_INTEGRATION_REVIEW.md](docs/FINAL_INTEGRATION_REVIEW.md)
- 구독형 CLI 기억 후보 추출: [docs/SUBSCRIPTION_MEMORY.md](docs/SUBSCRIPTION_MEMORY.md)
- 선택적 구독형 CLI–LangChain 브리지: [docs/SUBSCRIPTION_LANGCHAIN_PLAN.md](docs/SUBSCRIPTION_LANGCHAIN_PLAN.md)
- 자연어 제어층: [docs/NATURAL_LANGUAGE.md](docs/NATURAL_LANGUAGE.md)
- Loadout 통합 조회·설치·활성화 관리: [docs/LOADOUT_MANUAL.md](docs/LOADOUT_MANUAL.md)

공용 스킬은 `agent-hub skill catalog`으로 한 번에 조회한다. 이 명령은 원본 파일을 옮기거나 복제하지 않고, 등록 스킬과 설치된 Codex·Claude/Loadout·Antigravity 스킬을 출처·제공자 상태와 함께 읽기 전용으로 합친다. `agent-hub skill search --query ... --all-installed`, `agent-hub skill manual --output ... --all-installed`도 같은 통합 범위를 검색·설명서 생성에 사용한다. `agent-hub skill list`는 기존 호환성을 위해 등록된 공용 스킬만 표시한다.
- 기존 자산: `/home/sungt/agent-skills` (이 저장소와 독립적으로 보존)

전체 기능의 가상 실행 검증은 `python scripts/verify_feature_simulation.py`로
수행한다. 이 검사는 fixture 공급자·fake 모델·임시 Hub root만 사용하므로
구독형 CLI 호출, 실제 지식·작업 원장 변경, scheduler 등록을 하지 않는다.

이 저장소의 코드와 정책만 Git으로 관리한다. 실행 상태, 개인·회사 지식, 작업 로그, 캐시, 백업, 비밀값은 향후 별도 런타임 데이터 경계에 두며 추적하지 않는다.
