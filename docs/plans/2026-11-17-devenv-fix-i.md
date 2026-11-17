---
status: done
task: devenv-fix-i
---

# devenv 묶음 I: subagent 결과 보고를 띄운 id로 판별한다, 닫은 뒤의 원장 기록을 고친다

- 저장 위치: `docs/plans/2026-11-17-devenv-fix-i.md`
- 승인 근거(사용자 결정 2026-11-17, 그대로 인용):
  1. "subagent 결과 보고 판별을 이 세션이 띄운 subagent의 id로 한다. SubagentStart 기록에 있는 id이고 SubagentStop 기록 뒤에 도착한 것만 결과 보고로 인정한다. prompt_id와 글 모양에 기대는 판별은 보조로만 둔다. 이 세션에 없는 id나 아직 끝나지 않은 agent의 id를 쓴 글은 사용자 프롬프트로 처리하고 즉시 경고한다(현재 정책 유지)."
  2. "묶음 H (c)의 '닫은 뒤 CI 결과를 원장에 추가' 경로가 실제로 동작하지 않는 원인을 재현으로 찾아 고친다."
  3. "옛 모듈 이름을 가리키는 주석 2곳(hooks/discipline.py:1864, tests/bench_hooks.py:386)을 함께 고친다."
  4. "묶음 H 지적 1의 결과(prompt_seen에 프롬프트 글을 남기는지 여부)를 마감 기록에서 다시 확인해 보고한다."
  - "이 묶음은 강제 전환(11-22 이후)보다 먼저 끝낸다."
- push: 이 저장소의 커밋은 push하고 CI를 끝까지 따라간다(사용자 결정 2026-11-16 "push 허용: 각 작업의 커밋은 push하고 CI를 끝까지 따라간다").

## Context
묶음 H 뒤에도 승인이 끊기는 경로가 하나 남았다: subagent의 결과 보고가 **자기 턴으로** 도착할 때다. 리뷰를 띄울 때마다 승인이 끊겨 사용자가 `task:` 줄을 다시 적어야 한다. 또 원장은 닫힌 작업의 마지막 상태를 남기기는 하지만, 닫는 순간 상태가 낮아지거나 닫은 뒤의 CI 결과가 붙지 않는 것이 실사용에서 보였다.

## 확인한 사실 (2026-11-17, 이 세션의 hook 상태에서 승인과 무관한 키만 읽음)
- 자기 턴으로 온 결과 보고(게이트 분할의 리뷰, 12:06:40): hook이 받은 글의 첫 두 줄은 `agent-message`, `bracket`(= `<agent-message from="ID">` / `[Subagent hand-back] …`), `prompt_id`는 새 값, 판별은 `user`, 승인 `devenv-gate-split → 없음`. 모델에게 보이는 "Another Claude session sent a message:" 줄은 hook에 오지 않았다.
- 그 agent의 SubagentStart·SubagentStop 기록은 세션 상태 `agents`에 있었고(`on_agent_start` `hooks/discipline.py:644`, `on_agent_stop` `:689`), **stop 기록이 결과 보고 프롬프트보다 0.150초 먼저**였다. 관찰 1회다.
- 지금의 코드: `relay_agent_id(prompt, bare)`(`:2665`)는 머리줄 없는 꼬리표를 `bare`(= 진행 중인 프롬프트와 같은 `prompt_id`)일 때만 읽는다. `started_agent`(`:2683`)는 시작 기록만 보고 끝났는지는 보지 않는다. `on_prompt`(`:2915`)의 `relay = relay_shaped(prompt) or continues`.
- 원장(게이트 분할을 닫을 때): 닫기 직전 `LOCALLY_VERIFIED (12:03)`, 닫는 호출 직후 **`IMPLEMENTED (12:18)`**, 같은 내용 id. 그 6분 전(12:17)에 같은 내용으로 게이트가 통과했고 CI도 성공해 있었다. 그 뒤 마감 문서의 push와 CI 성공 뒤에도 줄은 그대로다. 묶음 H를 닫을 때는 `CI_VERIFIED`가 남았다(그때는 승인이 한 번도 끊기지 않았다). 차이: 게이트 분할에서는 중간에 승인이 끊겼다가 `task:` 줄로 다시 이어졌다.
  - 배제한 것: `content_late`에 있던 두 저장소(`C:\dev\tools`, `C:\dev\memo-board`)는 이 저장소와 겹치지 않아 `ledger_capped`(`:4131`)의 그 조건은 아니다. `approval_warning`은 지금 비어 있다.
  - 원인은 **미확인**이다. 후보: (a) 다시 이은 승인은 `approval_id`가 같아도 push·CI 기록(`pushes`, `ci`)이나 증거가 턴 기준으로 달리 읽힌다, (b) 닫는 호출의 PostToolUse 시점에는 `refresh_content`가 방금 바뀐 계획 문서 때문에 상태를 `IMPLEMENTED`로 계산한다, (c) 닫은 뒤의 Stop에서 `record_ledger`가 닫힌 경로로 들어가지 못한다(`stop_prompt_id`, `closed["content"]` 비교).
- `prompt_seen`: 지금 코드가 쓰는 항목의 필드는 `at, keys, prompt_id, chars, sha, lines, kind, before, after`뿐이다(글 없음). 다만 **이 세션의 상태 파일에는 고치기 전 코드가 쓴 항목이 아직 남아 있고 그 항목에는 `head` 필드(가린 첫머리 글)가 있다**(필드 이름만 확인, 값은 읽지 않음). 12개 한도로 밀려 나갈 때까지 남는다.
- 주석: `hooks/discipline.py:1864` "(test_approvals checks it)", `tests/bench_hooks.py:386` "(test_approvals T1 does the same)": 둘 다 이제 `tests/test_approvals_v2.py`에 있다.

## 설계
### 1. 결과 보고의 판별 (id 우선)
- 새 함수 `finished_agent(state, agent_id) -> bool`: `agents`에 그 id의 항목이 있고 `stopped`가 숫자일 때만 참. `started_agent`는 다른 곳(완료 알림의 task id 대조)에서 계속 쓴다.
- `relay_agent_id(prompt)`: 인자 `bare`를 없앤다. 머리줄이 있든 없든 첫 줄(머리줄이 있으면 둘째 줄)이 `<agent-message from="ID">` 하나뿐인 글에서 id를 읽는다. 읽는 것과 인정하는 것을 나눈다.
- `on_prompt`: `handback = relay_agent_id(prompt)`; `known = handback is not None and finished_agent(state, handback)`.
  - `relay = relay_shaped(prompt) or continues or known`.
  - 머리줄 없는 꼬리표인데 `known`이 아니면(이 세션에 없는 id, 아직 끝나지 않은 agent) 지금처럼 사용자 프롬프트로 처리되고(= `relay`가 아님, 또는 `continues`면 알림이지만 유지 조건을 못 채움) 승인이 끊기며 즉시 경고가 나간다. 경고의 사유에 "이 세션이 띄워 끝난 subagent의 결과 보고가 아님"을 더한다.
- `started_here` / `v2_keep_on_relay`: 결과 보고는 `finished_agent`일 때만 유지 조건을 따진다(머리줄이 있는 형태에도 같은 규칙: 사용자 결정 "SubagentStop 기록 뒤에 도착한 것만"). 그 밖의 유지 조건(v2 승인, 같은 git 루트, 지금 검증됨 또는 아직 묶이지 않음, 범위 있음)은 그대로다. 알림은 승인을 만들지 않는다.
- `prompt_id`와 글 모양은 보조로 남는다: `continues`는 턴의 기록(센 파일, 기준선)을 지키는 데, `relay_shaped`는 완료 알림을 가리는 데 쓴다. 결과 보고의 인정은 id 기록으로만 한다.
- 바뀌는 동작 하나(더 엄격해짐): 머리줄이 있는 결과 보고라도 그 agent의 stop 기록이 없으면 승인을 잇지 않는다. 기존 테스트 가운데 start만 기록하고 결과 보고를 보내는 사례가 있으면 그 기대값이 바뀐다 → 어떤 사례인지와 바꾼 이유를 기록에 적는다(기대값은 사용자 결정에 따른 것만 바꾼다).

### 2. 원장: 닫는 순간과 닫은 뒤
- 먼저 재현한다(테스트 먼저, 고치기 전에 실패를 본다). `tests/test_ledger.py`에 실제 흐름 그대로: 승인 → 편집 → 게이트 통과 → Stop(`LOCALLY_VERIFIED`) → 커밋·push·CI 성공 → **승인이 끊김(작업을 이름하지 않는 프롬프트) → `task:` 줄로 다시 이음** → 계획을 done으로 바꾸는 셸 호출 → 그 직후의 원장 → Stop → 문서 커밋·push·CI 성공 → Stop → 원장.
  기대: 닫는 호출 직후의 상태는 닫기 직전보다 낮지 않다(같은 내용에서). 닫은 뒤 같은 내용의 CI가 성공하면 `CI_VERIFIED` 줄이 붙는다.
- 재현되면 원인에 맞춰 고친다. 고치는 원칙: 원장은 Stop이 인정하는 것보다 많이 말하지 않는다(`ledger_capped`는 그대로), 원장은 기록 전용이다(아무 판정도 읽지 않는다), 닫힌 작업의 기억은 내용이 같을 때만 쓴다.
- 재현되지 않으면: 고치지 않고, 상태 파일에서 승인과 무관한 키(`pushes`, `ci`, `evidence`, `content`의 상태)로 좁힌 결과와 시도한 흐름을 적은 뒤 사용자에게 올린다(추측으로 고치지 않는다).

### 3. 주석 2곳
- `hooks/discipline.py:1864` → "(test_approvals_v2 checks it)". `tests/bench_hooks.py:386` → "(test_approvals_v2 T1 does the same)".

### 4. `prompt_seen` 재확인(보고)
- 코드와 테스트(H-T6)로 다시 확인: 새 항목에는 글이 없다.
- 남은 것: 고치기 전 코드가 쓴 항목의 `head`. 처리: `note_prompt_seen`이 항목을 더할 때 허용된 필드만 남기고 옛 항목을 걸러 쓴다(한 줄: 허용 필드 집합으로 다시 만듦). 테스트: `head`가 든 옛 항목이 있는 상태에서 프롬프트 하나가 오면 상태 파일에 `head`가 없다.

## 변경 범위
- `hooks/discipline.py`
- `tests/test_task_switch.py`
- `tests/test_approvals_v2.py`
- `tests/test_ledger.py`
- `tests/bench_hooks.py`
- `docs/plans/**`

## 테스트 (먼저 쓰고 실패를 확인)
| 사례 | 파일 |
|---|---|
| I-T1 끝난 subagent의 결과 보고가 자기 턴으로(새 `prompt_id`, 머리줄 없음) 오면 승인이 유지되고 경고가 없다 | `test_task_switch.py` |
| I-T2 같은 글인데 그 id의 stop 기록이 없으면(아직 도는 agent) 승인이 끊기고 즉시 경고 | 같은 곳 |
| I-T3 이 세션에 없는 id면 승인이 끊기고 즉시 경고 | 같은 곳 |
| I-T4 작업 중 도착(같은 `prompt_id`)도 같은 규칙: 끝난 agent만 유지 | 같은 곳 |
| I-T5 머리줄이 있는 형태도 stop 기록이 있어야 유지 | 같은 곳 |
| I-T6 유지 조건은 그대로: 다른 git 루트, 범위 없는 승인, 폐기된 승인은 끝난 agent의 결과 보고로도 잇지 못한다. 승인이 없던 세션에 결과 보고가 와도 승인이 생기지 않는다 | 같은 곳 |
| I-T7 꼬리표가 첫 줄이 아니거나 줄에 다른 글이 붙으면 결과 보고로 읽지 않는다 | 같은 곳 |
| I-T8 원장 재현(위 2의 흐름): 닫는 순간 낮아지지 않고, 닫은 뒤의 CI 성공이 붙는다 | `test_ledger.py` |
| I-T9 `prompt_seen`의 옛 항목에서 `head`가 사라진다 | `test_task_switch.py` |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-17-devenv-fix-i.md`에 저장한다(첫 편집 전에).
- [x] 2. I-T1~T7을 쓰고 실패를 확인한다. `finished_agent`, `relay_agent_id`, `on_prompt`, `started_here`를 고친다. 기대값이 바뀌는 기존 사례를 기록한다.
- [x] 3. I-T8로 원장을 재현한다. 재현되면 원인을 기록하고 고친다. 재현되지 않으면 고치지 않고 좁힌 결과를 기록한다.
- [x] 4. 주석 2곳, I-T9와 `note_prompt_seen`.
- [x] 5. 게이트 `python -I tests/run_all.py`(wall 기록).
- [x] 6. code-reviewer 1회(승인이 잘못 유지되거나 생기는 길이 있는지, id를 글로 위조하는 경우, stop 기록과 프롬프트의 순서). **이 결과 보고가 자기 턴으로 올 때 승인이 유지되는지가 1의 실사용 확인이다**(hook은 프로세스마다 새로 읽히므로 고친 코드가 바로 적용된다). 반영, 게이트.
- [x] 7. 커밋(코드/문서 분리), push, CI 확인.
- [x] 8. 마감 기록: 닫는 호출 직전·직후의 원장, 닫은 뒤 Stop과 CI 뒤의 원장(다음 턴에서 읽어 덧붙임), 실사용 확인 결과, `prompt_seen` 재확인 결과.

## 검증
- 각 단계의 모듈 테스트: `python -I tests/test_task_switch.py`, `python -I tests/test_ledger.py`, `python -I tests/test_approvals_v2.py`.
- 게이트(선언된 형태, 포그라운드) 통과와 CI 성공.
- 실사용: 단계 6의 리뷰 결과 보고 뒤 `prompt_seen`의 마지막 항목이 `relay`, `before == after == devenv-fix-i`인지 읽는다. 끊기면 그 자리에서 멈추고 `task:` 줄을 요청한다(끊긴 채 편집하지 않는다).

## 위험
- 승인 정책에 닿는 변경이다. 느슨해지는 곳은 하나: 자기 턴으로 온, 머리줄 없는 결과 보고를 **이 세션이 띄워 끝난 agent의 id일 때** 알림으로 읽는다. id는 사용자가 친 글에도 적을 수 있지만, 그 글을 보내는 사람이 승인 권한자인 사용자이고, 유지되는 것은 이미 있는 승인뿐이며 쓰기마다 다시 검증된다. 그 밖은 같거나 더 엄격해진다(stop 기록 요구).
- stop 기록이 결과 보고보다 늦게 도착하는 경우가 있으면(관찰은 1회, 0.15초 차이) 승인이 끊긴다: 안전한 쪽으로 실패하고 즉시 경고가 나간다. `prompt_seen`과 `agents`의 시각으로 계속 관찰한다.
- 다시 띄운(resume) agent는 `on_agent_start`가 `stopped`를 비우므로, 도는 동안 온 옛 결과 보고는 인정되지 않는다(의도한 동작).
- 원장 수정은 원인이 재현으로 확인될 때만 한다.
- 게이트 여유는 약 177초(wall 422초). 테스트가 늘어 wall이 500초를 넘으면 기록에 적는다.

## 진행 기록
- 2026-11-17 단계 2: I-T1~T7, I-T9를 먼저 쓰고 실패를 확인했다(5건: I-T1, I-T4, I-T5, I-T6, I-T9). `finished_agent`를 더하고 `relay_agent_id`(읽기만), `started_here`, `v2_keep_on_relay`, `on_prompt`, `approval_lost_note`(사유 "이 세션이 띄워 끝난 것으로 기록된 subagent의 결과 보고가 아님")를 고쳤다.
- **바꾼 기존 테스트 기대값 1건(사용자 결정 1에 따름)**: H-T7의 셋째 검사 가운데 "끝난 subagent의 결과 보고가 새 `prompt_id`로 오면 아무것도 잇지 못한다"는 새 규칙과 반대라 뺐다(같은 입력이 이제 I-T1이고 기대는 유지). 그 검사의 나머지 절반("진행 중인 id 아래의 다른 글은 아무것도 잇지 못한다")은 그대로 남겼다. 없는 id와 끝나지 않은 agent는 I-T2, I-T3이 본다. `tests/test_approvals.py`, `tests/test_approvals_v2.py`의 기대값은 바뀌지 않았다.
- 단계 3(원장): **원인을 테스트로 재현하지 못했다. 고치지 않았다.** I-T8로 세 흐름(승인이 끊겼다가 다시 이어진 뒤 닫기 / 닫기 전에 게이트를 한 번 더 / 그 사이에 추적되지 않던 파일을 커밋)을 돌렸고 셋 다 닫는 순간과 그 뒤의 Stop에서 `LOCALLY_VERIFIED`가 유지됐다. I-T8은 그 세 흐름을 고정하는 테스트로 남겼다.
  - 좁힌 것(이 세션의 상태에서 승인과 무관한 키만): 게이트 분할을 닫을 때 `approval_warning`은 없었고, `content_late`의 두 저장소는 이 저장소와 겹치지 않으며, 이 저장소는 내용으로 판정되는 root였다(`content_tracked_roots`). 리뷰가 든 후보(시각으로 판정되는 root에서 게이트 뒤의 편집이 cap을 건다)는 그때의 값(마지막 편집 11:55, 게이트 통과 12:17)과 맞지 않는다.
  - **이 묶음을 닫기 직전에 본 것(새 단서)**: 세션 상태의 `content`(내용 digest)는 마지막 Stop(13:01) 때의 것이고, 증거(게이트 통과 13:29)는 그 뒤에 고친 내용의 digest에 묶여 있어 둘이 다르다. 이 상태로 `ledger.root_state`를 계산하면 `IMPLEMENTED`다. 게이트 분할 때도 같은 모양이었다: 닫는 호출이 적은 줄의 내용 id가 그 전 Stop 때의 id와 같았다. 즉 **닫는 호출의 `refresh_content`가 내용을 새로 읽지 못하면**(또는 새로 읽은 값이 원장에 쓰이지 않으면) 옛 내용과 새 증거가 어긋나 `IMPLEMENTED`가 적힌다는 가설이 선다. 묶음 H를 닫을 때는 게이트와 닫기 사이에 Stop이 있어 내용이 이미 새로 읽혀 있었다. 이 가설은 아래 "닫는 호출 직후" 줄로 한 번 더 확인한다. 미확인인 채로 고치지 않는다.
- 단계 4: 주석 2곳(`hooks/discipline.py`의 `V2_FILES` 위, `tests/bench_hooks.py`)을 `test_approvals_v2`로 고쳤다. `note_prompt_seen`이 옛 항목을 지금의 필드 이름으로만 다시 만든다(I-T9).
- **사용자 결정 4의 재확인(묶음 H 지적 1의 결과)**: 지금 코드는 `prompt_seen`에 프롬프트의 글을 남기지 않는다(필드: `at, keys, prompt_id, chars, sha, lines, kind, before, after`; `lines`는 `line_kind`의 고정된 표지). 다만 묶음 H의 마감 기록이 말하지 않은 것이 하나 있었다: **이 세션의 상태 파일에는 고치기 전 코드가 쓴 항목이 남아 있었고 그 항목에는 가린 첫머리 글이 든 `head` 필드가 있었다**(필드 이름으로만 확인, 값은 읽지 않았다). 이 묶음의 수정이 적용된 뒤의 프롬프트에서 그 필드가 사라진 것을 상태 파일의 필드 이름 집합으로 확인했다(`head` 없음). 한계(리뷰): 걸러내는 기준이 필드 이름이라, 옛 버전이 `lines`나 `keys`에 글을 넣었다면 남는다. `head`를 쓰던 버전은 커밋된 적이 없어 그 버전의 `lines`가 무엇이었는지는 확인하지 못했다(그 버전에는 `lines` 필드가 없었다는 것은 이 세션의 필드 이름 집합으로 확인).
- 단계 5: 게이트 통과, `gate: 38 modules, sum 2529s, wall 435s, workers 6`. `test_task_switch`가 188 → 259초, `test_ledger`가 101 → 143초가 됐다.
- 단계 6 리뷰(code-reviewer 1회): **fix first**(승인이 잘못 유지되거나 생기는 길은 찾지 못함, 테스트 공백 2건). 반영:
  1. K-T13(`tests/test_approvals_v2.py`)이 start만 기록해, 범위 없는 승인의 검사에 닿기 전에 새 규칙에서 떨어지고 있었다(다른 이유로 통과) → `agent-stop`을 더해 범위 검사에 닿게 했다. 기대값은 그대로다. 계획의 I-T6에 적은 "범위 없는 승인, 폐기된 승인"은 `test_task_switch.py`에 따로 쓰지 않았다: 범위 없는 승인은 K-T13이, 닫힌·폐기된 승인과 다른 루트는 K-T8~K-T10(start와 stop을 모두 기록)이 본다.
  2. I-T6이 "끝남" 안내의 문구를, I-T2·I-T3이 새 사유 문구를 확인하게 했다.
  3. `finished_agent`: stop 시각이 실수이고 유한하며 start 시각보다 이르지 않을 때만 인정한다.
  - 반영 중의 실수: `math` import를 빼먹어 `ruff`가 F821을 냈고 바로 고쳤다. 그 사이의 테스트 실행에서 본 것: **프롬프트 hook이 예외로 죽으면 상태가 저장되지 않아 그 전의 승인 기록이 그대로 남는다**(I-T6이 유지된 것으로 나왔다). 새 프롬프트에서는 승인이 프롬프트 id에 묶여 있어 쓰이지 않겠지만 확인하지 않았다 → 별도 제안으로 올린다.
  - 기록만(반영하지 않음): 끝난 agent의 id는 resume이나 기록에서 밀려날 때까지 유효하다(일회용이 아니다). 사용자가 그 꼬리표로 시작하는 글을 넣으면 `task:` 줄 없이 이미 있는 승인이 이어진다: 새로 생기는 것은 없고 쓰기마다 다시 검증된다. 그 프롬프트에서는 테스트 공백 안내·위험 안내가 다음 사용자 프롬프트로 미뤄지고, 저장소 전환 안내와 `revoke`·`recover-state` 해석을 타지 않는다. 한도를 피하는 경로는 없다(`turn_files`와 기준선이 유지되어 더 엄격하다). `actor`의 주석 "no decision reads it"은 agent id에 대해서는 이제 맞지 않는다(R1 때부터). 닫는 호출의 원장 기록은 `worktree.budget_exhausted`를 보지 않는다(Stop은 본다).
- **실사용 확인(사용자 결정 1)**: 단계 6의 리뷰 결과 보고가 자기 턴으로 도착했다. `prompt_seen`의 그 항목: 13:14:16, 첫 두 줄 `agent-message`, `bracket`, 새 `prompt_id`, 판별 `relay`, 승인 `devenv-fix-i → devenv-fix-i`. 경고 없음. 그 agent의 stop 기록은 프롬프트보다 0.900초 먼저였다(게이트 분할 때는 0.150초). 같은 경우에 게이트 분할에서는 승인이 끊겼었다.
- 리뷰 반영 뒤 게이트 통과: `gate: 38 modules, sum 2532s, wall 434s, workers 6`.
- 단계 7: 커밋 `82d83f6`, CI run 44040270166 성공(1차), CI의 `gate: 38 modules, sum 1954s, wall 333s, workers 6`.
- 닫기 직전의 원장: `devenv-fix-i: LOCALLY_VERIFIED (리뷰 running), 2026-11-17 13:01, 내용 c24fc6a63bbe132f`. 닫기 직전의 세션 상태: 증거 `passed 13:29`, push `82d83f6`과 그 CI `verified`가 기록돼 있고, `content`의 digest는 증거의 digest와 다르다(위의 새 단서).
- **닫는 호출 직후의 원장**: `devenv-fix-i: CI_VERIFIED (리뷰 stale), 2026-11-17 16:47, 내용 ca254f91df4ec2db`. 닫기 직전(`LOCALLY_VERIFIED 13:01, 내용 c24fc6a63bbe132f`)보다 낮아지지 않았고, 내용 id가 새 값으로 바뀌었다: 닫는 호출의 `refresh_content`는 내용을 새로 읽었다. 세션 상태도 증거와 내용의 digest가 같아졌고 닫힌 작업의 기억에 새 내용 id가 고정됐다.
  - 따라서 위의 가설("닫는 호출이 내용을 새로 읽지 못한다")은 **이번 관찰로 기각한다.** 게이트 분할에서 닫는 호출이 `IMPLEMENTED`를 적은 원인은 여전히 미확인이다. 이번과 그때의 차이로 남은 것: 그때는 닫을 때 그 push의 CI 결과가 hook에 읽히지 않은 상태였고(push 뒤 Stop이 없었다), 같은 턴에 백그라운드 명령(메모리 표본)이 돌고 있었다. 이번에는 CI가 이미 `verified`로 읽혀 있었고 백그라운드 명령이 없었다. 두 조건은 I-T8에 없다.
  - 사용자 결정 2("닫은 뒤 CI 결과를 원장에 추가하는 경로가 동작하지 않는 원인을 재현으로 찾아 고친다")는 **끝내지 못했다**: 재현하지 못했고 고친 것이 없다. 다음에 시도할 것: I-T8에 "push는 기록됐지만 CI는 아직 읽히지 않은 상태에서 닫기"와 "같은 턴에 백그라운드 명령이 있는 상태에서 닫기"를 넣는다(가짜 `gh`가 필요하다: `tests/test_ci_status.py`의 방식을 쓴다).
- 닫은 뒤의 CI 결과가 원장에 붙는지: 이 기록의 커밋을 올린 뒤의 Stop 다음에 읽는다. 이번에는 닫을 때 이미 `CI_VERIFIED`라, 붙을 새 상태가 없다(문서만 바뀌면 내용 id가 같다). 이 경로의 실사용 확인은 "닫을 때 CI가 아직 안 끝난" 경우가 다시 올 때 할 수 있다.
