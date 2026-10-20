# Loadout 설명서

`agent-hub loadout --hub-root HUB catalog --project-root PROJECT`는 Hub 통합
모듈과 **공식 Loadout 플러그인 카탈로그 항목**을 함께 읽어 보여준다.
플러그인 경로는 Claude의 사용자 설치 기록에서 찾으며, 다른 설치는
`--plugin-root PATH`로 지정한다. `catalog_listed`는 플러그인 카탈로그에
등록된 항목이라는 뜻일 뿐, PROJECT에 설치됐거나 실행 중이라는 뜻이 아니다.
프로젝트를 지정하면 `managed_marker`가 `present` 또는 `absent`로 나온다.
이는 `CLAUDE.md`·`AGENTS.md`의 **공식 Loadout Store**
`<!-- store:NAME:start -->` 마커 검사 결과이며, Agent Hub 관리 마커도 실행 중인
세션의 로드 증명도 아니다. `project_installation`과 `installation_evidence`는
마커 또는 공식 스캐폴드 파일로 확인한 설치 근거를 따로 표시한다.
`instruction_marker`는 지침 블록, `scaffold_artifact`는 멀티에이전트 스캐폴드의
`_shared/orchestrator-rules.md`, `none`은 어느 근거도 찾지 못했음을 뜻한다.
따라서 스캐폴드형 `multiagent`는 마커가 `absent`여도 설치가 `detected`일 수 있다.
PROJECT를 주지 않으면 세 필드는 `not_checked`다. 이 값들은 과거 설치 이력이나
실행 중인 세션의 로드를 나타내지 않는다. 목록 조회는 조각의 설치·제거·설정을 수행하지 않는다.

## 통합 관리

`loadout manage`는 `--source`로 관리 대상을 구분한다. `--apply`가 없으면
설치·제거·활성화·비활성화 명령은 미리보기만 수행한다.
이 저장소의 가상환경을 사용하는 경우에는 `.venv/bin/activate`를 불러온 뒤
아래 명령을 실행하거나 `agent-hub` 대신 `.venv/bin/agent-hub`를 사용한다.

```bash
# Hub 내장 모듈의 설정 상태 확인과 활성화
agent-hub loadout --hub-root HUB manage --source hub --name skill-registry --action status
agent-hub loadout --hub-root HUB manage --source hub --name skill-registry --action enable --apply

# 공식 Loadout 설치기를 이용한 프로젝트 조각 설치·진단·제거
agent-hub loadout --hub-root HUB manage --source fragment --name agent-loop \
  --project-root PROJECT --flavor codex --action install
agent-hub loadout --hub-root HUB manage --source fragment --name agent-loop \
  --project-root PROJECT --flavor codex --action install --apply
agent-hub loadout --hub-root HUB manage --source fragment \
  --project-root PROJECT --action doctor
agent-hub loadout --hub-root HUB manage --source fragment --name agent-loop \
  --project-root PROJECT --flavor codex --action remove --apply

# guard의 전역 런타임 스위치: 조각 설치와 별도
agent-hub loadout --hub-root HUB manage --source guard-runtime --action status
agent-hub loadout --hub-root HUB manage --source guard-runtime --action enable --apply
```

Hub 모듈은 내장되어 있으므로 별도 설치·삭제가 아니라 enable/disable로
설정을 바꾼다. 기존 프로젝트 오버레이는 자동으로 갱신하지 않으며 필요한 경우
`verify`와 `overlay --replace-managed`를 사용한다. 이 설정값은 데몬이나
스케줄러의 실제 실행 상태를 나타내지 않는다.

`doctor`는 특정 조각이 아니라 프로젝트 전체의 Loadout 마커·배타 조건·부속 파일을
읽기 전용으로 검사하므로 이름과 flavor가 필요 없다. 조각에 대한 enable/disable은
각각 install/remove의 별칭이다. 이는 설정
파일에 조각을 넣거나 빼는 뜻이며 실행 중인 세션에 즉시 반영되거나 guard의
전역 런타임 스위치를 조작한다는 뜻이 아니다. 조각 변경은 공식 `store.py`의
미리보기와 충돌 검사를 먼저 통과해야 한다. 수정된 기존 부속 파일, 손상된
마커, 심볼릭 링크, 잘못된 Claude 설정과 수정된 coach 훅은 덮어쓰지 않는다.
기존 관리 블록이
설치기 정본과 다르면 보존하며, 내용을 확인한 후 `--replace-modified-block`을
명시하면 설치 또는 활성화 시 교체할 수 있다. 미리보기에도 이 옵션을 붙일 수
있으며 실제 변경에는 `--apply`가 별도로 필요하다. 실패 시 일반 조각의 대상
파일 내용과 모드를 사전 상태로 복구한다. 설치기는 동시 편집과 공유 잠금을
사용하지 않으므로 같은 파일의 다른 변경이 끝난 뒤 실행해야 한다.
멀티에이전트 스캐폴드는 빈 프로젝트에만
설치할 수 있고, 설치 실패 시 일부 파일이 남을 수 있으며 공식 설치기도 자동
제거를 지원하지 않는다. 다른 프로젝트 또는 기존 사용자 파일을 변경하려면
대상 경로와 flavor를 명시해 적절한 공식 절차를 따른다.

| 모듈 | 설명 | 상태 | 적용 대상 |
|---|---|---|---|
| improvement-observer | 제한된 자가개선 후보 관찰 | 켜짐 | codex, claude, antigravity |
| knowledge-mesh | 범위 분리 지식그물 저장소 | 켜짐 | codex, claude, antigravity |
| orchestration-handoff | 공통 원장 기반 오케스트레이터 전환 지침 | 켜짐 | codex, claude, antigravity |
| skill-registry | 공용 스킬 메타데이터 검색 및 설명서 | 켜짐 | codex, claude, antigravity |

기존에 Agent Hub가 생성한 프로젝트 오버레이는 `loadout overlay`에
`--replace-managed`를 붙여 최신 Loadout 내용으로 갱신할 수 있다. 이 동작은
정확한 Agent Hub 관리 표식이 있는 일반 파일만 원자적으로 교체하며, 사용자
파일과 심볼릭 링크는 거부한다.

현재 Hub 루트의 `AGENTS.md`와 `CLAUDE.md`는 각각 Codex 계열 공용 지침과
Claude의 프로젝트 자동 발견 지침으로, 생성된 오버레이를 읽도록 연결한다.
Antigravity CLI의 로컬 도움말은 프로젝트 지침 파일 자동 발견 이름을 명시하지
않으므로, 해당 CLI에서는 생성된 `.agent-hub/loadout.antigravity.md`를 명시적으로
읽히게 하거나 Hub 자연어 명령으로 전환을 요청해야 한다.

`agent-hub loadout verify --hub-root HUB --project-root PROJECT`는 세 표준
오버레이를 수정하지 않고 `current`, `missing`, `stale`, `unmanaged`, `unsafe`로
분류한다. `stale`만 `--replace-managed`로 갱신 대상이 될 수 있다.

## 자가개선 다중 검토 게이트

자동개선 후보는 Hub 원장에 기록된 `claude-main`(설계), `codex-main`(구현·시험)의
실제 실행 영수증이 모두 필요하다. `result.md`는 사람이 읽는 보고서일 뿐 자동 반영 근거가 아니다. 두 검토는 같은
변경 해시와 `pass` 결정이 있어야 한다.
하나라도 `return_to_design` 또는 실패이면 후보는 승격되지 않고 2번 설계 단계로
돌아간다. 큐 적재 명령은 원장 작업 ID인 `--task-id`를 반드시 받는다.

## 외부 호출 게이트웨이

Hub 내부의 구독형 LangChain, 구독형 기억 추출, 자연어 오케스트레이터 전환은
동일한 `ProviderGateway`를 통해서만 외부 CLI를 호출한다. 게이트웨이는 호출 전에
작업 원장에 채널별 단일 논리 요청을 예약하고, 제한·무응답·시간초과 시의 제공자
인계도 같은 영수증 안에서 처리한다. 재시작 뒤 같은 채널의 재호출은 예약 한도를
넘을 수 없으므로, 외부 요청을 조용히 중복 실행하지 않는다.

공식 Loadout의 `call_worker.sh`도 이 경계에 연결된다. 먼저
`agent-hub loadout bind-task`로 Loadout `task.md`의 `agent_hub_task_id`를 한 번
연결해야 하며, 연결되지 않았거나 이미 다른 Hub 작업에 연결된 경우 worker 호출은
실행 전에 거부된다. 실행마다 `external_dispatch_reserved`와 완료 영수증이 같은
Hub 작업 원장에 남는다.

## 완료 강제

작업은 보고만으로 완료되지 않는다. `agent-hub task complete TASK_ID --evidence ...`
는 활성 lease 또는 `reserved` 상태의 외부 worker 영수증이 하나라도 있으면 실패한다.
완료 뒤에는 새 lease·새 dispatch를 예약할 수 없다. Loadout 쪽에서는 acceptance
criteria를 모두 체크하고 `[VERIFICATION]`, `[COMPLETE]` 로그를 기록한 뒤 이 명령을
실행한다. 자동개선은 이 조건에 더해 위의 세 역할 검토 게이트까지 통과해야 승격된다.

독립 검토에는 `agent-hub natural ... --strict-provider --provider-timeout-seconds N`을
쓴다. strict 모드에서는 선택된 Claude/Antigravity가 제한 시간 안에 답하지 않거나
빈 응답을 내도 다른 제공자로 대체하지 않는다. 따라서 다른 모델의 성공을 독립 검토
통과로 오인할 수 없고, 실패 영수증을 근거로 설계 단계로 되돌릴 수 있다.

독립 검토는 작은 검증 질문 단위로 분해한다. Hub의 읽기 전용 Claude·Antigravity
adapter는 이 경로에 `low` 추론 예산을 명시해, 넓은 프롬프트 하나가 제한 시간을
소진하는 대신 각 기준의 통과·실패를 빠르게 영수증으로 남긴다. 한 기준이 시간초과면
그 기준만 미통과로 두고 재설계·재검토하며, 다른 provider의 handoff 성공으로 바꾸지 않는다.
