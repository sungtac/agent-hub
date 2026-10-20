# 자연어 제어층

`agent-hub natural --text "..." --mode basic`은 초보자 한국어 요청을
안전한 Hub 작업 계획으로 해석한다. 애매하거나 추가 식별자가 필요한
요청은 실행하지 않는다.

| 예시 요청 | 해석 | 자동 실행 |
|---|---|---|
| `자가개선 켜줘` | observer scheduler enable | 가능 |
| `자가개선 꺼줘` | Hub 태그 scheduler disable | 가능 |
| `점검 리스트 만들어줘` | 읽기 전용 점검 계획 | 프로젝트·새 출력 경로 필요 |
| `스킬 설명서 보여줘` | 공용 스킬 설명서 계획 | 새 출력 경로 필요 |
| `이 대화 기억해줘` | 명시 요청 memory extract | task ID·scope·안전한 메시지 필요 |
| `클로드를 오케스트레이터로` | provider handoff | task ID·대상 provider·계속 작업 지시 필요 |

지원 모드는 `basic`, `operations`, `development`다. 현재는 mode가
설명·권한 안내에 쓰이며, 위험 동작의 자동 승인에는 사용되지 않는다.
이 원칙은 자연어 오해가 지식 저장·복구·공급자 전환을 직접 실행하는
것을 막는다.

## 안전 실행

`--apply`를 붙여도 현재 실제 실행 가능한 요청은 `자가개선 켜줘`와
`자가개선 꺼줘`, 충분한 식별자가 있는 읽기 전용 작업, 명시 요청 기억
후보 추출, 그리고 명시 지시가 있는 오케스트레이터 전환이다. scheduler
작업은 Hub 태그가 붙은 crontab 행만 추가하거나 제거하므로 되돌릴 수 있다.

```bash
agent-hub natural --hub-root /home/sungt/agent-hub --text "자가개선 꺼줘" --apply
```

백업 복원과 지식 삭제는 자연어 `--apply`로 실행하지 않는다. 기억 저장과
공급자 전환은 아래처럼 모든 식별자를 제공한 owner의 명시 요청에만 실행한다.

## 단계형 읽기 전용 실행

필요한 입력이 모두 있으면 점검 리스트, 스킬 설명서, 스킬 검색은
`--apply`로 실행할 수 있다.

```bash
agent-hub natural --hub-root /home/sungt/agent-hub \
  --text "점검 리스트 만들어줘" --apply \
  --project-root /home/sungt/agent-hub \
  --output /home/sungt/agent-hub/runtime/reports/checklist.json
```

출력 경로는 반드시 새 파일이어야 하며 기존 보고서를 덮어쓰지 않는다.

## 자연어 기억 추출

명시 요청을 실제 후보 추출로 실행하려면 작업 정본과 범위, 대상 대화를
모두 제공해야 한다. 결과는 지식그물이 아니라 owner 승인 후보 큐로만
들어간다.

```bash
agent-hub natural --hub-root /home/sungt/agent-hub \
  --text "이 대화 기억해줘" --apply \
  --task-id task-... --scope personal \
  --provider auto \
  --message "사용자는 한국어로 간결한 답변을 선호한다고 명시했다."
```

`--message`는 반복 가능하다. 민감정보 패턴은 provider에 보내기 전에
차단되며, 구독 사용량 제한·timeout·network 오류는 task ledger의
provider handoff 규칙을 따른다.

## 자연어 오케스트레이터 전환

전환은 provider 내부 대화를 복사하지 않는다. 같은 task ID의 append-only
원장·승인·산출물을 기준으로 대상 provider가 다음 작업을 실행한다. 따라서
Codex CLI, Claude CLI, Antigravity CLI 어느 쪽에서 시작했는지와 무관하다.
활성 lease가 남아 있으면 살아 있는 작업을 강제로 빼앗지 않고 거절한다.
같은 작업에서 별도의 전환을 요청할 때는 요청마다 새로운 `--switch-id`를 지정한다.
같은 ID의 재시도는 중복 실행을 막기 위해 거절된다. ID를 생략한 기존 호출은
종전처럼 작업당 한 번만 허용된다. ID에는 민감한 내용을 넣지 않는다.

자동 기록을 사용하려면 각 전환에 `--shared-handoff`를 명시한다. 이 옵션은
제공자가 작성한 공유용 JSON의 목표, 완료 조건, 완료 작업, 검증, 남은 문제,
다음 행동을 동일 task의 원장에 기록하고, 다음 전환에 가장 최근 기록을 전달한다.
원시 대화는 기록하지 않는다. 이 옵션은 해당 내용을 다음 제공자에게 공유해도 되는
경우에만 사용한다. 형식이 틀리거나 알려진 민감정보 패턴이 감지되면 기록을 거부한다.
패턴 검사는 모든 민감정보를 식별하지 못하므로 공유 전 내용의 적합성은 요청자의 책임이다.
옵션이 없으면 종전대로 결과 본문은 원장에 저장하거나 전달하지 않는다.

```bash
agent-hub natural --hub-root /home/sungt/agent-hub \
  --text "클로드를 오케스트레이터로 전환해줘" --apply \
  --task-id task-... --provider claude \
  --switch-id switch-1 \
  --shared-handoff \
  --instruction "원장을 읽고 승인된 다음 단계를 계속 진행해줘."
```

사용량 제한·timeout·네트워크 불가 때는 같은 task ID에서 가용 구독형
provider로 handoff한다. 성공·실패·전환 결과는 모두 원장에 남고, 실행 뒤
lease는 해제된다.
