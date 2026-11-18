---
status: done
task: devenv-fix-a
---

# devenv 결함 개선 A: 이 세션이 시작한 것의 완료 알림에서는 승인을 유지한다 (결함 2, 12)

- 묶음 계획: `docs/plans/2026-11-16-devenv-final-check-fixes.md` 단계 2
- 저장 위치: `docs/plans/2026-11-16-devenv-fix-a.md`
- 승인 근거: 사용자 지시 2026-11-16 "드러남 결함 상세히 확인후 결함 개선하자"

## Context
승인된 계획으로 작업하던 세션이, 브라우저 확인용 개발 서버를 백그라운드로 띄웠다 내린 뒤 "승인 범위 밖 파일 23개"로 막혔다. 사용자가 `task:` 줄을 다시 적어야 이어졌다. 백그라운드 명령과 subagent를 쓰는 정상 흐름마다 생기는 일이다.

## 확인한 사실
- **재현했다**(2026-11-16, 임시 저장소와 격리된 승인 폴더에서 hook을 차례로 호출): ExitPlanMode 승인 → 계획 파일 저장 → 편집 13개 → 백그라운드 Bash → Stop → 편집 → **`<task-notification>` 프롬프트** → 승인이 `None`이 되고, 다음 Write가 "이번 턴에 승인 범위 밖 파일을 이미 15개 수정했습니다(한도 5)"로 거부된다. 테스트 세션에서 본 문구와 같다. 알림 전까지는(Stop을 포함해) 승인이 유지됐다.
- 따라서 결함 12(같은 프롬프트 안에서 승인이 사라짐)는 결함 2와 **같은 것**이다. 알림은 대화 기록에 사용자 줄로 남기 전에 hook에 도착한다(기록의 `promptId`가 바뀌지 않은 이유. 이 부분은 추정이다).
- 원인 코드: `v2_keep_on_relay`(`hooks/discipline.py:2275`)는 `relay_agent_id(prompt)`가 이 세션이 시작한 subagent의 id일 때만 승인을 유지한다. `relay_agent_id`는 `Another Claude session sent a message:` 다음 줄의 `<agent-message from="ID">`만 읽는다(`discipline.py:2253`). `<task-notification>`으로 시작하는 프롬프트는 id를 읽지 않고 버린다.
- 알림의 모양(이 세션과 테스트 세션의 기록): 첫 줄 `<task-notification>`, 둘째 줄 `<task-id>ID</task-id>`. 백그라운드 Bash의 id는 짧은 영숫자(`bos8h2vfr`), subagent 완료 알림의 id는 그 subagent의 id(`a136c084…`, SubagentStart가 기록하는 것과 같다).
- 백그라운드로 실행된 Bash의 도구 결과에는 `backgroundTaskId` 키가 있다(테스트 세션 기록의 도구 결과 5건). PostToolUse hook이 받는 `tool_response`에 같은 키가 오는지는 **구현 첫 단계에서 실제 payload로 확인한다**(아니면 멈추고 다시 설계한다).
- 지금의 유지 조건(R1, 그대로 둔다): 들어온 승인이 v2 포인터일 것, 승인의 저장소와 지금의 저장소가 같을 것, 그 자리에서 승인을 다시 검증해 통과할 것, 범위가 있는 승인일 것. 유지된 뒤에도 쓰기마다 다시 검증한다.
- 기존 테스트: `tests/test_approvals.py`의 K-T1~K-T13(`k_session`, `k_relay`, `edit_decision`). K-T4 "a task notification: dropped"는 **모르는 id**의 알림이다: 그대로 통과해야 한다.

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. 시작을 hook이 본 id의 알림만 인정(추천) | 백그라운드 명령은 PostToolUse의 `backgroundTaskId`를, subagent는 SubagentStart의 id를 세션 상태에 적어 두고, 알림의 `<task-id>`가 그 안에 있을 때만 R1과 같은 조건으로 유지 | 알림 글만으로는 아무것도 얻지 못한다. 유지의 근거가 "이 세션이 그 작업을 시작했다는 hook 자신의 기록"이다 |
| B. `<task-notification>`이면 모두 유지 | 모양만 본다 | 누가 붙여 넣은 글이나 다른 세션의 알림으로도 유지된다. 지금의 "중계는 엄격하게만"이라는 원칙을 깬다 |
| C. 승인을 프롬프트가 아니라 세션에 묶는다 | 알림과 무관하게 유지 | 승인의 수명을 크게 넓힌다(P0-04의 설계를 뒤집는다). 이 결함에 필요한 것보다 훨씬 큰 변경 |

## 설계
`hooks/discipline.py`:
- 세션 상태에 `background_tasks`(이 세션이 시작한 백그라운드 작업의 id 목록, 최대 50개)를 둔다. `on_post`의 Bash·PowerShell 가지에서 `tool_response`가 dict이고 `backgroundTaskId`가 `[A-Za-z0-9_-]{1,64}` 모양의 문자열이면 적는다. `on_fail`(실패 이벤트)에서도 같은 함수를 부른다.
- `relay_task_id(prompt) -> str | None`: 빈 줄을 뺀 첫 줄이 정확히 `<task-notification>`이고 바로 다음 줄이 정확히 `<task-id>ID</task-id>`(같은 id 모양)일 때만 ID. 그 밖의 모양은 None.
- `v2_keep_on_relay`: 지금의 "시작한 subagent의 결과 보고" 판정에 더해, `relay_task_id`가 `background_tasks`에 있거나 시작한 subagent의 id일 때도 다음 조건으로 넘어간다. 그 뒤의 조건(같은 저장소, 다시 검증, 범위 있음)과 반환은 한 줄도 바꾸지 않는다.
- 유지되지 않을 때의 안내(`RELAY_NOTE`)와 그 밖의 중계 처리(파일 수 유지, 승인을 만들지 않음)는 그대로다. v1 경로(휴면)는 건드리지 않는다.

## 변경 범위
- `hooks/discipline.py`
- `tests/test_approvals.py`
- `docs/plans/**`

## 테스트 (`tests/test_approvals.py`의 K 묶음에 추가, 먼저 작성해 실패 확인)
| 사례 | 기대 |
|---|---|
| K-T14 이 세션이 백그라운드로 시작한 명령의 완료 알림 | 같은 승인이 유지되고 알림 프롬프트에 묶인다. 범위 안 편집 허용, 범위 밖 거부 |
| K-T15 재현 사례: 편집 여럿 → 백그라운드 명령 → 알림 → 편집 → Stop | 편집이 허용되고 Stop이 "승인된 계획 없이"·"승인된 작업이 없습니다"를 말하지 않는다 |
| K-T16 시작한 subagent의 완료 알림(`<task-id>`가 그 subagent의 id) | 유지 |
| K-T4(기존) 모르는 id의 알림 | 버림(그대로) |
| K-T17 다른 세션이 시작한 백그라운드 작업의 id | 버림 |
| K-T18 모양이 어긋난 알림: id 줄이 둘째 줄이 아님, 앞에 다른 글, `<pasted_content>`로 감싼 것, id에 공백·꺾쇠, `<task-notification>` 한 줄짜리 | 모두 버림 |
| K-T19 승인이 없던 세션에 아는 id의 알림 | 승인이 생기지 않는다 |
| K-T20 알림 전에 계획 본문이 바뀜 / 폐기됨 / 다른 저장소에서 도착 / 범위 없는 승인 | 모두 버림(R1과 같은 결과) |
| K-T21 `backgroundTaskId` 기록: 문자열이 아님, 모양이 어긋남, Bash·PowerShell이 아닌 도구의 결과, 51번째 | 적지 않는다(50개 한도) |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-fix-a.md`에 저장한다(첫 편집 전에).
- [x] 2. PostToolUse의 실제 payload에 `backgroundTaskId`가 있는지 확인한다(이 세션에서 백그라운드 명령을 하나 실행하고 hook이 받은 것을 본다: hook 코드에 임시 출력을 넣지 않고, 격리된 복사본 hook이나 기록으로 확인할 방법을 먼저 찾는다. 확인할 수 없으면 "기록의 도구 결과에 있다"는 근거로 진행하고 미확인으로 적는다).
- [x] 3. 테스트 K-T14~21을 쓰고 실패를 확인한다.
- [x] 4. 구현.
- [x] 5. 게이트.
- [x] 6. code-reviewer 1회: "알림으로 승인이 늘어나는 길이 있는가"를 따로 보게 한다. 반영, 게이트.
- [x] 7. 커밋, push, CI 확인, 마감 기록.

## 검증
- `python -I tests/run_all.py` 통과(끝줄의 wall 기록), CI 성공(run id). 상태 목표: CI_VERIFIED
- 실사용: 이 세션 자체가 백그라운드 명령과 subagent를 쓰므로, 수정 뒤 이 세션에서 알림 뒤에도 편집이 이어지는지 관찰한다(hook이 새 코드를 쓰는지 포함해 기록한다).

## 위험
- 승인을 더 오래 유지하는 변경이다. 막는 장치: id는 hook이 직접 본 시작 기록에서만 온다 / 유지 조건과 쓰기마다의 재검증은 그대로다 / 알림은 승인을 만들지 못한다.
- 백그라운드 작업의 id를 아는 다른 글이 같은 세션에 들어오는 경우(예: 사용자가 알림 글을 붙여 넣음): 승인을 준 사람이 사용자 자신이므로 권한이 늘지 않는다. 도구 출력은 프롬프트가 될 수 없다.
- 시간 초과로 백그라운드로 넘어간 명령에도 `backgroundTaskId`가 오는지는 미확인이다. 오지 않으면 그 경우는 지금처럼 버려진다(안전한 쪽).

## 진행 기록
- 2026-11-16 단계 2: PostToolUse payload를 직접 보지는 못했다(운영 설정에 hook을 더하는 것은 별도 승인 항목). 간접 근거: 세션 기록의 도구 결과에 `backgroundTaskId`가 있고(요청한 백그라운드 3건, 시간 초과로 넘어간 것 8건 모두 9글자 영숫자 문자열), hook이 이미 ExitPlanMode의 `plan`·`filePath`를 기록된 도구 결과와 같은 키로 `tool_response`에서 읽어 동작한다. **미확인으로 남긴다.**
- 단계 3: K-T14, K-T15, K-T16이 고치기 전에 실패(승인 `None`, 편집 거부, Stop이 "승인된 작업이 없습니다")하는 것을 확인했다.
- 단계 4: `hooks/discipline.py`에 `note_background_task`, `relay_task_id`, `started_here`, 상태 키 `background_tasks`. `v2_keep_on_relay`는 판정 한 곳만 바꿨다.
- 단계 6 리뷰(code-reviewer 1회): "승인이 늘어나는 길은 찾지 못했다". 낮은 등급 3건 반영: subagent가 띄운 백그라운드 명령도 센다는 것과 응답 없는 실패 이벤트는 적지 않는다는 것을 docstring에 명시, 테스트 추가(subagent 이벤트, 응답 없는 실패, CRLF, 저장된 값이 list가 아닌 경우).
- 게이트: `python -I tests/run_all.py` 통과 3회(wall 526초, 475초, 486초; 37개 모듈). 600초 한도까지의 여유가 약 75~125초로 줄었다(`test_approvals` 406~443초가 가장 길다). 별도 제안: `test_approvals`를 둘로 나누기.
- 커밋 `e44c977`, CI run 43805353519 성공(job hooks). 상태: CI_VERIFIED.
- 실사용 미확인: 수정 뒤 이 세션에 완료 알림이 두 번 왔지만 그 사이에 사용자의 새 프롬프트가 있어 승인 유지 여부를 가려 볼 수 없었다. 모델에게 보이는 알림에는 `[SYSTEM NOTIFICATION - NOT USER INPUT]` 머리글이 붙는데, 세션 기록에는 `<task-notification>`으로 시작하는 글로 남는다. hook이 어느 쪽을 받는지는 확인하지 못했다(머리글이 붙은 채 온다면 이 수정은 듣지 않고 지금처럼 버려진다: 안전한 쪽). 다시 하는 최종 확인에서 본다.
