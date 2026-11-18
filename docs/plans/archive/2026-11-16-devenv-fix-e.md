---
status: done
task: devenv-fix-e
---

# devenv 결함 개선 E: 위험 점수 안내가 사실대로 말하게 하고, 보안 감사가 기본 확인 두 가지를 할 수 있게 한다 (결함 11, 4·9)

- 묶음 계획: `docs/plans/2026-11-16-devenv-final-check-fixes.md` 단계 6, P5 단계 2
- 저장 위치: `docs/plans/2026-11-16-devenv-fix-e.md`
- 승인 근거: 사용자 지시 2026-11-16 "드러남 결함 상세히 확인후 결함 개선하자", "진행해"

## Context
두 가지 작은 결함이다. (1) 위험 점수 안내가 "진행 중인 계획에 대안 비교가 없습니다"라고 하는데, 오늘 이 세션의 계획에는 모두 `## 대안 비교` 절이 있었는데도 턴마다 그 말이 나왔다. (2) 보안 감사 subagent가 의존성 감사와 "비밀 파일이 이력에 있는가" 확인을 하지 못했다.

## 확인한 사실
- 안내 문구: `hooks/discipline.py:3528` `RISK_NOTE`. 판정은 `hooks/risk.py:133` `compares_alternatives`(제목이나 표 머리줄의 낱말)와 `:154`(계획 글 목록 중 하나라도 비교가 있으면 안내하지 않음).
- **원인(오늘 확인)**: `note_risk`는 계획 글을 `_approved_plan_texts(state)`(`:3536`)로 찾는데, 이 함수는 `state["approval"]`만 본다. Stop은 `note_risk`를 부르기 **전에** 승인을 다시 검증하고, 계획이 done으로 닫혔거나 승인이 사라졌으면 `state["approval"] = None`으로 만든다. 그러면 계획 글 목록이 비고, "비교가 있는 계획이 하나도 없음"이 되어 안내가 나간다. 근거: `docs/plans/2026-11-16-devenv-fix-a2.md:22`에 `## 대안 비교`가 있는데 그 작업의 턴 뒤에도 안내가 나왔다(오늘 여러 번).
- 최종 확인에서 본 경우(계획에 "대안이던 … 제외"라는 문장만 있음)는 다른 경우다: 계획은 찾았지만 제목·표가 없었다. 문구가 "없습니다"라고 단정하는 것이 문제였다.
- `agents/security-auditor.md:12-13`: 의존성은 "`uvx pip-audit` (against the uv lock/requirements export)"라고만 한다. 최종 확인에서 subagent는 `uv export … -o "$TEMP/…"`를 골랐고 auto mode 분류기가 거부했다.
- 임시 파일 없는 방법을 실행해 확인했다(2026-11-16, `C:\\dev\\memo-board`): `uv run --frozen --with pip-audit pip-audit --progress-spinner off` → "No known vulnerabilities found", 종료 코드 0, 저장소 변경 없음(`git status` 비어 있음). 프로젝트 자신(`memo-board`)은 PyPI에 없어 건너뛴다는 줄이 나온다(정상). auto mode의 subagent에서 분류기가 이 명령을 통과시키는지는 **미확인**이다.
- 이력 확인: 묶음 D(`5e9dc8b`) 뒤로 `git log --all --oneline -- .env` 꼴이 guard를 통과한다(`tests/test_guard.py`의 사례).

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. 안내가 보는 계획을 "이 턴이 일한 승인"으로 하고, 문구를 경우별로 나눈다(추천) | Stop이 재검증 전에 잡아 둔 승인(묶음 C에서 만든 `held`)을 `note_risk`에 넘긴다. 계획을 찾았는데 제목·표가 없으면 "제목이나 표로 된 대안 비교가 없습니다", 승인된 계획 자체가 없으면 "승인된 계획 없이 한 변경입니다"라고 말한다 | 판정 규칙(낱말 찾기)은 그대로 두고 틀린 말만 없앤다 |
| B. 문구만 고친다 | "제목이나 표로 된 비교가 없습니다"로 | 계획에 비교가 있는데도 안내가 나오는 오늘의 경우가 남는다 |
| C. 안내를 끈다(`CLAUDE_RISK_SCORE=0`) | — | P2-03의 관측을 버린다. 틀린 안내의 원인이 밝혀졌으므로 끌 이유가 없다 |

## 설계
- `hooks/discipline.py`: `note_risk(state, held=None)`. `on_stop`이 재검증 전에 잡은 승인 포인터를 넘긴다. `_approved_plan_texts`는 `state["approval"]`이 없으면 `held`의 `base`·`task_id`로 계획 글을 찾는다. 안내 문구를 둘로 나눈다: 계획 글이 있는데 비교가 없을 때(`RISK_NOTE`의 문장을 "제목이나 표로 된 대안 비교가 없습니다"로), 계획 글이 하나도 없을 때("승인된 계획 없이 한 변경입니다. 되돌리기 어려운 변경이면 /plan으로 대안을 적고 승인받으세요"). 점수 계산, 관측 기록, 안내 기준, "알림이며 차단이 아님"은 그대로다.
- `hooks/risk.py`의 판정 함수는 바꾸지 않는다(문구 선택에 필요한 것만 돌려주게 할 수는 있다: 구현 때 가장 작은 쪽으로).
- `agents/security-auditor.md`: 의존성 감사 줄을 실행해 확인한 명령으로 바꾼다(`uv run --frozen --with pip-audit pip-audit --progress-spinner off`, 임시 파일을 만들지 않는다고 명시). 비밀 이력 확인 줄에 guard가 통과시키는 꼴을 적는다(`git log --all --oneline -- <파일>`; `-p`나 파이프를 붙이면 막힌다). 명령이 권한·분류기에 막히면 다른 길로 돌아가지 말고 "실행하지 못함"으로 보고하라는 한 줄.
- `tests/test_docs_consistency.py`: agent 정의가 그 두 명령을 말하는지, 그리고 그 git 명령이 실제로 guard를 통과하는지(guard를 직접 불러 확인)를 고정한다.

## 변경 범위
- `hooks/discipline.py`
- `hooks/risk.py`
- `agents/security-auditor.md`
- `tests/test_risk.py`
- `tests/test_docs_consistency.py`
- `docs/plans/**`

## 테스트 (먼저 작성해 실패 확인)
| 사례 | 위치 | 기대 |
|---|---|---|
| 승인된 계획에 `## 대안 비교`가 있고, 계획을 done으로 닫은 턴에 점수가 높음 | `test_risk.py`(hook 경유) | 안내 없음(지금은 "없습니다" 안내가 나감) |
| 승인된 계획에 비교 제목·표가 없고 점수가 높음 | 같은 곳 | 안내가 "제목이나 표로 된"을 말한다 |
| 승인된 계획 없이 점수가 높음 | 같은 곳 | 안내가 "승인된 계획 없이"를 말하고 "진행 중인 계획에"를 말하지 않는다 |
| 점수가 낮음 / `CLAUDE_RISK_SCORE=0` | 기존 사례 | 그대로 |
| agent 정의의 두 명령 문구, git 명령이 guard 통과, `-p`를 붙이면 차단 | `test_docs_consistency.py` | 지금 실패(문구 없음) |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-fix-e.md`에 저장한다(첫 편집 전에).
- [x] 2. 테스트를 쓰고 실패를 확인한다.
- [x] 3. 구현(hook), agent 정의 수정.
- [x] 4. 게이트.
- [x] 5. code-reviewer 1회. 반영, 게이트.
- [x] 6. 커밋, push, CI 확인, 마감 기록.

## 검증
- `python -I tests/run_all.py` 통과(끝줄의 wall 기록), CI 성공(run id). 상태 목표: CI_VERIFIED
- 실사용 1: 이 묶음을 닫은 뒤의 프롬프트에 틀린 안내가 나오지 않는지 본다.
- 실사용 2(묶음 A2의 확인): 이 계획을 승인받는 동안 짧은 백그라운드 명령 하나를 걸어 두어, 승인과 저장 사이에 알림이 오게 한다. 저장 뒤 Stop hook이 승인 유실을 말하지 않으면 A2가 실사용에서 확인된 것이다. 결과를 기록한다.
- agent 정의는 새로 뜨는 subagent부터 읽힌다: 다시 하는 최종 확인에서 감사가 두 확인을 했는지 본다(미확인으로 남긴다).

## 위험
- agent 정의의 변경이다(P4-03의 일부를 앞당김). 범위는 두 줄과 한 줄 추가로 한정한다. P5-04(model 줄)는 건드리지 않는다.
- `uv run --with`는 uv 캐시에 임시 환경을 만든다(프로젝트 밖). 네트워크로 PyPI 권고 DB를 조회한다.
- 안내가 줄어든다: "승인된 계획에 비교가 있으면 안내하지 않음"이 이제 계획을 닫은 턴에도 적용된다. 관측 기록(점수와 종류)은 그대로 남는다.

## 진행 기록
- 2026-11-16 단계 2: `test_docs_consistency.py`의 D7이 실패(문구 없음), `test_risk.py`의 R21이 오류(함수가 `held`를 받지 않음)인 것을 확인했다.
- 단계 3: `hooks/discipline.py`에 `note_risk(state, held)`, `_approved_plan_texts(state, held)`, 문구 `RISK_NOTE`(고침)·`RISK_NOTE_NO_PLAN`·`RISK_NOTE_NO_TEXT`, `on_stop`의 `worked_under`. `agents/security-auditor.md` 두 곳. `hooks/risk.py`는 바꾸지 않았다.
- **"확인한 사실"의 정정**: "계획이 done으로 닫혔으면 계획 글 목록이 비어 안내가 나간다"는 틀렸다. 계획을 done으로 닫으면 범위 안의 변경은 승인이 덮은 것으로 처리돼 그 턴의 변경 목록에서 빠지고(`refresh_content`의 `turn_covered`), 채점할 것이 없어 안내도 관측 기록도 생기지 않는다(리뷰 반영 중 디버그로 확인). 안내가 틀리게 나가는 경우는 Stop의 재검증이 **다른 이유로** 승인을 비울 때(계획 본문 변경, 폐기, 미저장), 그리고 승인이 Stop 전에 이미 사라졌을 때다. 오늘 반복된 안내가 각각 어느 경우였는지는 가려 보지 않았다(묶음 D의 승인 유실, 한 턴에 계획 여럿이 이어진 경우가 섞여 있다).
- 단계 5 리뷰(code-reviewer 1회, "fix first"): Stop의 판정과 `ledger_closed` 처리는 바뀌지 않는다고 확인됐다. 반영 4건: (1) R21·R22가 배선을 검증하지 않았다(호출을 예전으로 되돌려도 통과) → hook을 거치는 R23 추가. R23은 호출을 예전 형태로 되돌리면 실패하고 고친 형태에서 통과하는 것을 직접 확인했다. (2) agent 문구 "it writes no file"이 사실과 달랐다(`uv run`은 `.venv`를 만들거나 맞춘다. `git status`가 비어 보인 것은 `.venv`가 무시 목록에 있어서다) → "추적되는 파일은 바꾸지 않는다, `.venv`는 만들거나 맞출 수 있다, 맞춰진 환경을 감사하므로 pip-audit 자신의 패키지도 나온다, `uv.lock`이 없으면 실행하지 못함으로 보고"로 고쳤다. (3) D7이 guard의 종료 코드 2만 봐서 guard가 죽어도 "통과"로 읽혔다 → 0/2/그 밖을 구분. (4) 승인은 있는데 계획 글을 찾지 못한 경우에 "승인된 계획 없이"라고 말하던 것 → 셋째 문구.
- 리뷰가 범위 밖으로 남긴 것(제안): `agents/security-auditor.md:11`의 `git log -p -S<pattern>`은 diff로 비밀 값을 출력할 수 있어 새 절의 "never print the file"과 어긋난다(P4-03에서 다룬다).
- 규칙 이탈: 디버그와 테스트 수정에서 heredoc 패치를 두 번 더 썼다(오늘 네 번). 결과는 일치 검사·ruff·테스트로 확인했다.
- 게이트: `python -I tests/run_all.py` 통과 2회(wall 468초, 491초; 37개 모듈).
- 커밋 `2c30ca8`, CI run 43861193940 성공(job hooks, 1차). 상태: CI_VERIFIED.
- 실사용 2(묶음 A2의 확인): 이 계획의 승인과 저장 사이에 백그라운드 명령의 완료 알림이 도착하게 했다. 그 뒤의 Stop hook이 "승인된 계획(task: devenv-fix-e)"을 말했다: 승인이 살아남았다. **묶음 A2는 실사용에서 확인됐다.**
- 그 Stop hook은 `test_approvals.py`를 "이 계획의 범위 밖 변경"으로 보고했다: 같은 사용자 프롬프트 아래에서 먼저 승인받아 끝낸 묶음 A2의 파일이다(커밋 `885d250`). 알림으로 이어진 턴을 hook이 한 턴으로 세어, 앞 계획의 변경을 뒤 계획의 범위 밖으로 잡는다. 되돌리지 않았다.
- 실사용 1(닫은 뒤 틀린 안내가 없는지)과 agent 정의의 효과는 이 기록 뒤에야 볼 수 있다: 미확인으로 남긴다.
