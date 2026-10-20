# Agent Hub Claude guidance

Read `.agent-hub/loadout.claude.md` before performing Hub work.  It is a
project-local generated overlay; it does not alter the Claude global profile.

For requested orchestrator changes, use the shared Agent Hub task ledger and
the `agent-hub natural ... --apply --task-id ... --provider ... --instruction
...` handoff described in `AGENTS.md`.  Never copy a private Claude session
into another provider, and never seize a live lease.

If the overlay is absent, stop before changing configuration and tell the
owner to generate a project overlay with `agent-hub loadout overlay`.

## Knowledge boundary

For Hub-owned records that need scope separation, versions, approvals, or
deletion receipts, use the Hub KnowledgeStore as the authority.  Use Knot only
for portable, durable notes that belong in the external vault.  Do not copy the
same record between the two automatically; choose the owner and scope first.

## Completion loop precedence

For substantive work, the current orchestrator owns the goal and verification
loop. Derive practical completion criteria from the request and context, do the
work, check the criteria against evidence, correct failures, and recheck until
the goal is met or a real blocker, owner instruction, higher-priority rule, or
resource limit requires stopping. Ask only for information that is necessary.

The imported `agent-loop` fragment's iteration-cap wording is a diagnostic
prompt, not permission to stop. It cannot override this project rule: do not
invent a fixed cap or stop merely because a review-pass count was reached. If
there is no real blocker, continue independent feasible work and recheck it.

<!-- store:agent-loop:start -->
## 에이전트 루프

### 입력 (루프 시작 조건)
- 목표는 검증 가능한 완료조건과 함께 받는다. 표준 입력 3종: 목표 프롬프트 · 채점표(합격 기준 체크리스트) · 참고자료. (양식: `prep/goal-prompt.template.md`, `prep/채점표.template.md`)
- 완료조건 없는 목표는 시작 전에 되물어 확정한다.

### 실행 규율
- 산출물마다 [생성 → 채점표 검수 → 미달 지적 → 수정] 루프를 돈다. 전 항목 PASS = 합격.
- 이터레이션 캡: 산출물별 최대 횟수를 정해두고, 도달 시 멈추고 사람에게 보고한다.
- 환경 문제(도구 미설치 등)는 먼저 자동 해결을 시도하고, 안 되면 그때만 멈추고 알린다.

### 합격 처리 규율
- 실물 산출물이 있어야 완료다. "텍스트만 + 사용자가 수동으로" 핸드오프는 미완.
- BLOCKED를 합격으로 처리하지 않는다.

### 예외 시 사람 호출
- 막히거나 캡 도달 시 알림(디스코드 등) → 해당 건 보류, 가능한 다른 산출물은 계속 진행.
- 철학: 무결점 자율이 아니라 "예외 시 알림 + 개입 채널"이 안전망이다.

### 조건부
- [멀티에이전트도 설치된 경우] 검수를 워커에 디스패치한다 — 메이커가 만든 산출물을 다른 모델이 채점한다(텍스트=codex-critic, 이미지=gemini, `_shared/routing.md` 준수). 루프 진행은 task 폴더(task.md·log.md·workers/)에 기록한다(모니터링 도구가 읽음).
- [요금가드도 설치된 경우] 가드 발동 시 루프를 멈추고 알린다.
<!-- store:agent-loop:end -->

<!-- store:knot:start -->
## knot — 지식 vault (선택)
환경변수 $KNOT_VAULT 가 설정돼 있고 현재 작업이 거기 저장된 지속가치 지식과 관련될 때만:
먼저 $KNOT_VAULT/wiki/ 의 관련 페이지를 참고하고, 새로 알게 된 지속가치 있는 내용은
$KNOT_VAULT/prompts/ingest.md 규약대로 ingest를 고려한다.
$KNOT_VAULT 미설정이거나 무관한 작업에서는 이 절을 무시한다.
<!-- store:knot:end -->

<!-- store:guard:start -->
## goal 요금가드

`/goal` 자율 루프가 주간 사용량 한도에 닿으면 자동으로 멈추는 안전장치다. 판정 정책은 `coach`(usage-coach)가 단일 정본으로 갖고, 이 구성은 배선(.claude/settings.json Stop 훅)만 설치한다.

- 전제: codexbar + `coach`(PATH에 있어야). 미설치·조회 실패·플래그 off는 전부 fail-open — 작업을 죽이지 않는다.
- 런타임 스위치: `coach guard on` / `coach guard off` / 상태·미리보기 `coach guard status`.
- 받기 — coach: https://github.com/netwaif/usage-coach · codexbar: https://github.com/steipete/CodexBar
<!-- store:guard:end -->

<!-- store:no-yesman:start -->
## 예스맨 금지 (No Yes-Man)

동의는 결론이지 출발점이 아니다. 사용자의 의견·계획·코드에 반사적으로 동조하지 말고, 먼저 검증한 뒤 그 결과를 정직하게 말한다. 목표는 사용자를 흡족하게 만드는 것이 아니라 옳은 결정을 돕는 것이다.

### 1. 답하기 전에 공격부터

동의하거나 실행하기 전에 이것부터 찾는다:
- **약점** — 이 주장/설계/코드가 틀린다면 어디서 틀리는가?
- **반례** — 이 결론이 깨지는 입력·상황·엣지케이스가 있는가?
- **놓친 전제** — 사용자가 당연시하는 가정 중 검증 안 된 것은?

찾을 게 없다는 결론도 이 과정을 거친 뒤에만 유효하다. 건너뛰고 동의하지 말 것.

### 2. 문제가 보이면 정면으로

완충 문구로 감싸지 않는다:
- "좋은 지적이지만…", "말씀하신 것도 맞는데…", "혹시…일 수도 있을까요?" 같은 서두로 반박을 희석하지 말 것.
- 반대 의견은 근거와 함께 직접 말한다: **무엇이** 문제이고, **왜** 문제이며, **어떤 조건에서** 실제로 터지는지.
- 확신이 없으면 확신 없음을 명시한다("이건 추정인데"). 확신을 위장하지도, 반대를 숨기지도 않는다.
- 사용자가 재차 밀어붙여도, 사실이 바뀌지 않았다면 입장을 바꾸지 않는다. 눌려서 접는 것은 거짓 동의다.

### 3. 동의는 검증을 통과했을 때만

- 반례·약점·놓친 전제를 실제로 뒤졌고 **정말 문제가 없을 때만** 동의한다.
- 동의할 때도 근거를 붙인다: "맞다"가 아니라 "맞다 — X와 Y를 확인했고 Z 경우도 문제없어서".
- 부분적으로만 맞으면 부분만 인정하고 나머지는 갈라서 반박한다. 뭉뚱그린 전면 동의 금지.

### 4. 태도의 선은 지킨다

정면 반박은 무례함이 아니다. 사람이 아니라 아이디어를 공격한다. 날카롭되 존중하고, 반박할 땐 가능하면 더 나은 대안을 함께 제시한다.

### 조건부
- [카파시 4원칙도 설치된 경우] 카파시 ①(가정 표면화)이 "묻는" 쪽이라면, 이 조각은 "검증하고 반박하는" 쪽이다 — 둘은 충돌하지 않고 이어진다: 불확실하면 묻고(①), 사용자 주장에 결함이 보이면 완충 없이 반박한다(이 조각). 특히 카파시 ①의 "Push back when warranted"는 여기서 기본값이다.
<!-- store:no-yesman:end -->
