# 의존성 출처와 잠금 정책

| 항목 | 고정 버전 | 출처 | 용도 | 상태 |
|---|---|---|---|---|
| uv | 0.13.1 | Astral 공식 설치 문서·PyPI | 격리 Python·의존성 관리 | 설치됨 |
| Python | 3.12.15 | uv 관리 CPython 배포 | 전체 하네스 런타임 | 설치됨 |
| LangGraph | 1.2.15 | PyPI/공식 소스 | 상태·승인·재개 | 설치됨 |
| LangGraph SQLite checkpoint | 3.1.2 | PyPI/공식 소스 | 재개용 체크포인트 캐시 | 설치됨 |
| LangMem | 0.0.30 | PyPI/공식 소스 | 후보 기억·개선 | 설치·격리 후보 경로 연결됨; live model 추출 검증 전 |
| DeepAgents | 0.7.25 | PyPI/공식 소스 | 스킬·서브에이전트 | 설치·등록/범위/읽기 전용 권한 경계 연결됨 |
| LangChain Core | 1.6.10 | PyPI/공식 소스 | 선택적 구독형 CLI–LangChain 텍스트 facade | lock에 고정됨; C1 출력 정규화 시험 통과, 실제 facade는 미구현 |
| bubblewrap | 0.6.1 | Ubuntu 22.04 패키지 | 자가개선 테스트 격리 | 설치됨·namespace 시험 통과 |

잠금 파일은 실제로 격리 환경에서 해결·시험한 의존성만 포함한다. 인증 정보·토큰·개인 경로는 잠금 파일이나 보고서에 저장하지 않는다.
