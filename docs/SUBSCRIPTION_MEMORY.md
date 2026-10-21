# 구독형 CLI 기억 후보 추출

Antigravity 구독 로그인은 API 키가 아니다. Hub는 이를 LangMem의 내장
모델로 가장하지 않고, 구독형 CLI 기반의 별도 후보 추출기로 사용한다.
추출 결과는 항상 `MemoryCandidateStore`에만 들어가며 owner 승인 전에는
지식그물 검색·DeepAgents memory에 노출되지 않는다.

## 허용 trigger

- `explicit-request`: owner가 기억 추출을 명시 요청함
- `task-completed`: 작업 완료 후 확정된 결과를 검토함
- `repeated-preference`: 반복된 선호가 충분한 근거와 함께 확인됨

그 외 자동 trigger는 CLI 단계에서 거부된다. observer는 기억 추출을
자동 실행하지 않는다.

## 안전 경계

- 입력은 최대 6,000자 JSON이며 API key·token·password·식별번호 패턴이
  보이면 외부 CLI로 보내기 전 거부한다.
- 출력은 JSON schema, 최대 3개 후보, 허용 kind, confidence, evidence,
  600자 content 제한을 모두 통과해야 한다.
- API key, provider 전역 설정, CLI 대화 상태는 저장하지 않는다.
- `limited`, `unavailable`, timeout, 손상 JSON은 후보를 만들지 않는다.

## 사용법

```bash
agent-hub memory-candidate --hub-root /home/sungt/agent-hub \
  extract-cli \
  --scope personal \
  --task-id task-... \
  --trigger explicit-request \
  --evidence "owner requested durable preference extraction" \
  --messages-file messages.json

agent-hub memory-candidate --hub-root /home/sungt/agent-hub list --scope personal
agent-hub memory-candidate --hub-root /home/sungt/agent-hub promote --id candidate-...
```

`messages.json`은 message object 배열이다. 민감한 원문은 넣지 않는다.

## 공급자 전환

`extract-routed-cli`는 task ledger의 lease를 사용해 Codex, Claude,
Antigravity 중 지정 provider 또는 `auto` provider로 추출한다. 사용량 제한,
네트워크 unavailable, timeout이면 결과를 원장에 남기고 다음 가용 CLI로
handoff한다. 일반 실패나 품질 검증 실패는 재실행하지 않는다.

## 현재 로컬 수용 검증

2026-10-15에 로그인된 Antigravity CLI `1.3.5`로 personal task의 명시 요청
후보 추출을 실제 실행했다. `provider=antigravity`, `fallback=false`, 성공 실행
기록과 lease 해제를 확인했고, 결과는 owner 승인 전 `candidate` 큐에만 적재됐다.
같은 내용은 운영 지식그물 검색에 나타나지 않았다. Antigravity가 스키마 결과 뒤에
도구 요약 JSON을 덧붙이는 경우, Hub는 첫 번째의 완전한 엄격 후보 문서만 허용한다.

```bash
agent-hub memory-candidate --hub-root /home/sungt/agent-hub \
  extract-routed-cli \
  --scope personal --task-id task-... \
  --provider antigravity --trigger explicit-request \
  --evidence "owner requested durable preference extraction" \
  --messages-file messages.json
```
