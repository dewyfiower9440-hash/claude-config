---
status: done
task: devenv-fix-c
---

# devenv 결함 개선 C: 계획을 닫은 작업의 마지막 검증 상태가 원장에 남게 한다 (결함 7)

- 묶음 계획: `docs/plans/2026-11-16-devenv-final-check-fixes.md` 단계 4, P5 단계 2
- 저장 위치: `docs/plans/2026-11-16-devenv-fix-c.md`
- 승인 근거: 사용자 지시 2026-11-16 "드러남 결함 상세히 확인후 결함 개선하자", 순서 결정 "B 마감 → 옛 계획 정리 → P5 계획 → 묶음 C~F"

## Context
`/implement`는 시작할 때 원장에서 작업의 마지막 검증 상태를 읽는다. 그런데 원장은 끝난 작업을 실제보다 낮게 보여 준다: 오늘 CI까지 성공한 `devenv-fix-a`가 `IMPLEMENTED (리뷰 stale)`로 남아 있다. 최종 확인에서 본 것(`memo-expiry: LOCALLY_VERIFIED`만 있고 CI 성공이 없음)과 같은 일이다.

## 확인한 사실
- 재현(2026-11-16, 이 저장소): `python -I hooks/ledger.py show --task devenv-fix-a` → `IMPLEMENTED (리뷰 stale), 13:19`. 실제는 커밋 `e44c977`의 CI run 43805353519 성공, 문서 커밋 `1d9b257`의 run 43807341619 성공.
- `hooks/discipline.py`의 `record_ledger`(`:3643`)는 Stop 때 `active_approval(state, stop_prompt_id)`가 있을 때만 적는다.
- `on_stop`은 그 전에 승인을 다시 검증한다(`:3699-3709`). 계획이 `done`으로 닫혀 있으면 `v2_write_check`가 거부하고(사유에 `plan_status:done`), `state["approval"] = None`이 된다. 그래서 **계획을 닫은 턴의 Stop은 아무것도 적지 못한다.**
- 닫은 뒤의 턴(CI 결과가 나중에 오는 경우)에는 승인이 이미 폐기돼 있어 역시 적지 못한다.
- `ledger_capped`(`:3613`)는 `state["approval_warning"]`이 있으면 상태를 `IMPLEMENTED`로 낮춘다. 닫은 턴에는 그 안내("계획이 done")가 `approval_warning`에 들어간다.
- 지켜야 할 기존 원칙(테스트 R-T16, R-T20 `tests/test_ledger.py`): 승인 없는 편집은 적지 않는다, 승인이 맡지 않은 프롬프트의 Stop은 적지 않는다, 원장은 Stop보다 높은 상태를 말하지 않는다, 원장은 기록일 뿐 판정에 쓰이지 않는다.
- `ledger.record`는 같은 작업의 마지막 기록과 상태·내용이 같으면 적지 않는다(`hooks/ledger.py:313`).

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. 닫힌 작업을 세션이 기억해 그 내용이 그대로인 동안만 이어 적는다(추천) | Stop에서 승인이 "계획 done"으로 끝나면 `{task, approval id, base, 내용 id}`를 세션 상태에 남긴다. 그 턴과 그 뒤의 Stop에서, 유효한 승인이 없고 저장소 내용 id가 그때와 같으면 그 작업 이름으로 지금의 검증 상태를 적는다. 내용이 달라지면 기억을 지운다 | 적는 대상이 "이 세션이 승인 아래 끝낸 바로 그 내용"으로 한정된다. 다른 작업의 변경이 섞이지 않는다 |
| B. 닫은 턴의 Stop에서만 한 번 적는다 | 기억을 다음 턴으로 넘기지 않는다 | CI 결과가 다음 턴에 오는 흔한 경우를 놓친다(오늘 본 것) |
| C. 승인과 무관하게 계획 파일의 task로 적는다 | 저장소의 done 계획을 읽어 적는다 | 승인 없이 한 변경도 그 작업의 검증으로 적힌다. R-T16의 원칙을 깬다 |

## 설계 (`hooks/discipline.py`)
- `on_stop`: 재검증 전에 들고 있던 승인 포인터를 잡아 둔다. 재검증이 `plan_status:done`으로 거부하면 `state["ledger_closed"] = {task_id, approval_id, base}`를 남긴다(다른 거부 사유에서는 남기지 않는다: 폐기·본문 변경·저장소 불신은 정상 종료가 아니다).
- `record_ledger`: 유효한 승인이 있으면 지금처럼. 없고 `ledger_closed`가 있으면 그 작업으로 적되, (1) 처음 적을 때의 내용 id를 `ledger_closed["content"]`에 고정하고, (2) 그 뒤에는 지금의 내용 id가 그것과 같을 때만 적으며, 다르면 `ledger_closed`를 지우고 적지 않는다. 새 승인이 생기면 `ledger_closed`를 지운다(다른 작업이 시작됐다).
- `ledger_capped`: 닫힌 작업으로 적을 때는 "계획이 done"이라는 그 안내 하나만 낮춤 사유에서 뺀다(같은 글자인지 비교). 다른 경고와 나머지 낮춤 사유는 그대로다.
- 적는 내용의 형식, 원장 파일, `ledger.py`는 바꾸지 않는다. 판정(Stop의 허용·차단)은 한 줄도 바꾸지 않는다.

## 변경 범위
- `hooks/discipline.py`
- `tests/test_ledger.py`
- `docs/plans/**`

## 테스트 (`tests/test_ledger.py`의 "through the Stop hook" 묶음에 추가, 먼저 작성해 실패 확인)
| 사례 | 기대 |
|---|---|
| R-T21 승인 아래 편집 → 게이트 통과 → 계획을 done으로 닫음 → Stop | 그 작업의 마지막 기록이 `LOCALLY_VERIFIED`(지금은 기록 없음 또는 `IMPLEMENTED`) |
| R-T22 닫은 뒤 다른 프롬프트의 Stop, 내용 그대로 | 기록이 늘지 않는다(상태가 같으므로). 상태가 낮아지지도 않는다 |
| R-T23 닫은 뒤 파일을 고치고 Stop | 그 작업으로는 아무것도 적지 않는다(R-T16과 같은 원칙) |
| R-T24 그 뒤 원래 내용으로 되돌려도 | 다시 적지 않는다(기억이 지워졌다) |
| R-T25 계획을 닫지 않고 본문을 바꿔 승인이 폐기된 경우 | 적지 않는다 |
| R-T26 닫은 뒤 새 승인이 생긴 경우 | 닫힌 작업으로 적지 않는다 |
| 기존 R-T14~R-T20 | 그대로 통과 |

`ci: required`인 저장소에서 CI 결과가 다음 턴에 와 `CI_VERIFIED`로 올라가는 것은 hook 테스트로 만들기 어렵다(push와 CI 읽기 기록이 필요). `ledger.root_state`의 그 분기는 이미 단위 테스트가 있으므로, 이 묶음에서는 실사용(이 저장소의 다음 작업)으로 확인하고 결과를 기록한다.

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-fix-c.md`에 저장한다(첫 편집 전에).
- [x] 2. 테스트 R-T21~26을 쓰고 실패를 확인한다(`python -I tests/test_ledger.py`, 약 25초).
- [x] 3. 구현.
- [x] 4. 게이트.
- [x] 5. code-reviewer 1회(닫힌 작업의 이름으로 다른 변경이 적힐 길이 있는가, Stop의 판정이 바뀌는가). 반영, 게이트.
- [x] 6. 커밋, push(P5 계획 문서 커밋 `e897180`도 함께 올라간다), CI 확인, 마감 기록.
- [ ] 7. 실사용 확인: 이 계획을 닫고 CI가 끝난 뒤 `ledger.py show --task devenv-fix-c`의 상태를 기록한다.

## 검증
- `python -I tests/run_all.py` 통과(끝줄의 wall 기록), CI 성공(run id). 상태 목표: CI_VERIFIED

## 위험
- 세션 상태에 키가 하나 늘어난다(`ledger_closed`). 예전 세션 상태에는 없으므로 없을 때의 동작이 지금과 같아야 한다(테스트로 확인).
- 닫은 턴의 문서 커밋은 내용을 바꾼다: 닫은 뒤 게이트를 다시 돌리지 않으면 상태는 `IMPLEMENTED`로 적힌다. 사실대로이며, 원장이 Stop보다 높게 말하지 않는다는 원칙과 맞는다.
- 이미 낮게 적힌 옛 기록(`devenv-fix-a` 등)은 고치지 않는다(원장은 덧붙이기만 한다).

## 진행 기록
- 2026-11-16 단계 2: R-T21과 R-T26이 고치기 전에 실패하는 것을 확인했다(닫은 턴에 기록 없음). R-T22~25는 기록이 전혀 없어도 통과하는 사례라 R-T21에 기대어 의미가 있다.
- 단계 3: `hooks/discipline.py`에 상태 키 `ledger_closed`, `on_stop`의 기억, `record_ledger`의 닫힌 작업 경로, `ledger_capped`의 `closed_note`. 패치 스크립트를 heredoc으로 만들었다(CLAUDE.md §9 위반, 이번 세션 두 번째). 일치 개수 검사·`ast.parse`·테스트로 확인했고 깨진 곳은 없다. 리뷰 반영분은 Write로 만든 스크립트로 적용했다.
- 단계 5 리뷰(code-reviewer 1회, "fix first"): Stop의 판정이 바뀌는 길은 없다고 확인됐다. 반영 4건: (1, 높음) 닫은 뒤 같은 턴에 고친 파일이 닫힌 작업 이름으로 적힐 수 있었다(관측 모드에서는 승인이 무효여도 편집이 막히지 않는다) → `closed_as_done`이 "이 턴이 바꾼 것이 닫힘을 처음 본 때 승인이 덮은 것, 또는 그 저장소의 계획 파일뿐"일 때만 기억한다(R-T27). (2, 중간) 저장소 오류로 거부된 Stop도 닫힘으로 보던 것 → 지금 걸린 거부 사유가 바로 그 done 사유일 때만(R-T29). (3) 이번 프롬프트에 묶인 승인만 인정(`active_approval`). (4) 닫는 Stop에서 내용이 고정되지 않았으면 다음 Stop에서 지운다(`pin`, R-T30). 추가: 낮춤 예외가 그 안내 하나에만 적용됨(R-T28), 옛 세션 상태(R-T30).
- 확인하지 못한 것: R-T27이 리뷰 반영 전 코드에서 실패하는지 따로 돌려 보지 않았다. 리뷰 지적 2는 hook 수준의 재현 없이 단위 사례로만 고정했다.
- 게이트: 1회차 통과(wall 565초, 다른 세션의 게이트와 겹침). 리뷰 반영 뒤 1회 **실패**: `tests/test_discipline_d3.py:88`이 작업 폴더 `C:\dev\_disc_d3\bt11`을 지우지 못함(`PermissionError [WinError 32]`). 원인은 다른 Claude 세션이 `C:\dev\_snap-devenv-1453` 사본에서 전체 게이트를 돌리며 같은 고정 폴더를 쓴 것(프로세스 목록으로 확인). 그 오류 글이 `tests/run_all.py:103`의 출력에서 `UnicodeEncodeError`(cp949)를 일으켜 러너가 중단됐다. 그 세션이 끝난 뒤 재실행해 통과(37개 모듈, wall 471초).
- 커밋 `b66c0db`. CI run 43817718487: 1차 **실패**(`test_handoff`의 "H-T8 a commit made since: the record says so -> False", 1건; 러너 wall 573초), 재실행(2차) 성공. 이번 변경은 그 검사가 타는 코드를 건드리지 않았고 로컬에서는 통과한다. 원인은 확정하지 못했다(추정: 느린 러너에서 시간 제한이 있는 git 호출이 실패해 문구가 빠짐, `hooks/handoff.py:162, 286`). 상태: CI_VERIFIED(2차 기준).
- 별도 제안으로 남기는 결함 3건(이 묶음에서 고치지 않음): (a) 테스트의 고정 작업 폴더 때문에 저장소 사본 두 곳에서 게이트를 동시에 돌릴 수 없다, (b) `tests/run_all.py:103`이 cp949로 찍지 못하는 글자에서 러너째 죽어 뒤 모듈의 결과가 사라진다, (c) `test_handoff` H-T8이 부하에서 흔들린다.
- 단계 7(실사용 확인)은 이 기록을 커밋한 뒤 Stop을 거쳐야 볼 수 있다: 결과는 묶음 계획(`2026-11-16-devenv-final-check-fixes.md`)의 진행 기록에 적는다.
