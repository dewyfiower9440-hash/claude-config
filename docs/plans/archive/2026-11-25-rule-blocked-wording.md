---
status: done
task: rule-blocked-wording
---

# 계획 승인 규칙의 틀린 문장 하나 고치기, 그 뒤 릴리스

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-25-rule-blocked-wording.md`
- 배경: `rule-and-state-ttl` 계획의 구현은 끝났고 게이트를 통과했다(45개 모듈, 2636개 검사). 커밋 전 코드 리뷰가 fix-first로 판정했다: 사용자가 승인한 규칙의 글(`rules/plan-approval.md`) 가운데 `blocked`에 관한 문장이 hook의 실제 동작과 다르다. 승인받은 글을 바꾸는 일이고 그 계획은 이미 `done`이라, 고칠 문장을 다시 승인받는다. 변경은 아직 커밋도 반영도 되지 않았다.
- push: 사용자 결정 인용(2026-11-25) "push 여부 … 전부 승인"에 따라, 이 릴리스도 merge 뒤 push하고 CI를 끝까지 따라간다.

## 목표
규칙의 `blocked` 문장을 사실에 맞게 고치고, 리뷰가 느슨하다고 본 테스트 두 곳을 조인 뒤, `rule-and-state-ttl`의 변경과 함께 커밋·릴리스·push한다. 비목표: hook 코드 변경, 규칙의 다른 문장 변경, `blocked`의 동작 자체를 바꾸기.

## 조사 결과
- `hooks/approvals.py:373-375`: 계획의 status가 `in-progress`가 아니면 `Verdict("plan_status:<status>", revoke=True)`: 승인이 취소된다(직접 읽어 확인).
- `hooks/discipline.py:2834-2859`(`v2_rearm`): ACTIVE 상태의 승인 기록만 다시 무장한다. 취소된 기록은 status를 되돌리고 `task:<id>` 줄을 적어도 이어지지 않는다(리뷰어 보고, 코드 위치는 직접 확인).
- 따라서 `blocked`로 바꾼 계획은 승인을 영구히 잃고, 그 세션은 그 저장소를 수정할 수 없다. 풀리는 길은 계획을 다시 승인받는 것(ExitPlanMode)과 `revoke task:<id>`뿐이다. 지금의 문장 "A plan parked as `blocked` is continued only after the user sets it to `in-progress` again"은 틀렸다. 내가 hook 코드를 확인하지 않고 쓴 문장이다. `CLAUDE.md` 2절의 "grants no approval and revokes the one it had"와도 어긋난다.
- 리뷰의 낮은 등급 지적: `tests/test_docs_consistency.py`의 언어 규칙 판별 정규식이 머리말의 닫는 `---`에서 멈추지 않는다(본문에 `paths:`가 있는 파일을 언어 규칙으로 본다). D13의 문서 대조가 접두 일치라 `cleanupPeriodDays`가 9여도 문서의 "90"에 걸려 통과한다.
- 리뷰가 "정책이지 hook의 동작은 아니다"라고 한 것: "Two repositories, two plans"(한 계획의 변경 범위에 다른 저장소의 파일을 적을 수 있다). 의도한 정책이라 그대로 둔다.

## 변경 범위
- C:/dev/_wt/claude-config/rules/plan-approval.md
- C:/dev/_wt/claude-config/tests/test_docs_consistency.py
- C:/dev/_wt/claude-config/docs/plans/**
- C:/dev/_wt/claude-config/tests/_baseline/**

## 가정
- 이미 만들어 둔 미커밋 변경(`rules/plan-approval.md`, `tests/test_docs_consistency.py`, `settings.json`, `hooks/retention.md`, 닫힌 `rule-and-state-ttl` 계획서, 계획 파일 보관 이동)은 사용자가 승인한 `rule-and-state-ttl` 계획의 결과다. 이 계획은 그 위에 문장 하나와 테스트 두 곳만 고치고, 함께 커밋한다.
- 편집을 모두 끝내고 계획서 기록까지 쓴 뒤에 리뷰어를 돌린다(결과 보고가 승인을 끊으면 수정이 거부된다).
- 리뷰가 다시 수정이 필요하다고 하면, 고치지 않고 멈춰서 보고한다(세 번째 계획을 바로 내지 않는다: 같은 접근이 두 번 실패한 것이므로 사용자와 방향을 정한다).

## 선택한 접근
문장을 사실대로 고친다. 대안은 `blocked`가 승인을 취소하지 않게 hook을 바꾸는 것이지만, `CLAUDE.md`가 이미 "blocked는 승인을 취소한다"고 정해 두었으므로 규칙을 따르게 한다.

## 고칠 문장 (`rules/plan-approval.md`의 다섯째 항목)
지금:
```markdown
- **What is done to the plan file does not bring an approval back.** Closing, moving or
  deleting it after the approval is gone changes nothing. A plan parked as `blocked` is
  continued only after the user sets it to `in-progress` again.
```
고친 뒤:
```markdown
- **What is done to the plan file does not bring an approval back.** Closing, moving or
  deleting it after the approval is gone changes nothing. A plan parked as `blocked` has
  lost its approval for good: setting it back to `in-progress` and a `task:<id>` line do
  not continue it. It is presented again and approved (ExitPlanMode), or laid down with
  `revoke task:<id>`.
```

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-25-rule-blocked-wording.md`에 저장(`status: in-progress`). `docs/plans`의 파일이 10개를 넘으므로 닫힌 계획 `2026-11-19-guard-b15.md`를 `archive/`로 옮긴다 — 검증: 승인본과 status 줄만 다름, 최상위 계획 파일 10개.
- [x] 2. 테스트 먼저(`tests/test_docs_consistency.py`): (a) 규칙이 "`blocked` has\n  lost its approval for good"을 말하고 "is\n  continued only after"를 말하지 않는다는 검사, 그리고 그 근거인 코드(`approvals.py`가 `in-progress`가 아닌 status에 `revoke=True`를 돌려줌)를 함께 확인. (b) 언어 규칙 판별을 머리말 블록 안의 `paths:`로 한정. (c) D13의 문서 대조를 숫자 뒤에 숫자가 이어지지 않는 일치로 — 검증: (a)가 지금 문장에서 실패함을 확인.
- [x] 3. `rules/plan-approval.md`의 문장을 위의 글로 고친다 — 검증: `python -I tests/test_docs_consistency.py` 통과.
- [x] 4. 게이트 `python -I tests/run_all.py`. 진행 기록을 쓰고 마지막 편집으로 `status: done`(머리말 블록 포함) — 검증: `GATE PASSED`.
- [ ] 5. `code-reviewer`에게 전체 미커밋 변경을 보임 → ship이면 커밋(규칙과 테스트 / 설정과 `retention.md` / 계획서 둘과 보관 이동) → 릴리스(`/release-hooks`: 지연 측정과 기준선 커밋, `git -C ~/.claude merge --ff-only devenv-final`, 실제 hook 확인, `~/.claude`에서 게이트) → push → CI를 끝까지 확인 — 검증: CI run id와 결과.

## 리스크와 대응
- 규칙이 모든 세션에 실린다: 틀린 문장이 또 있으면 모든 프로젝트에 영향 → 이번 리뷰가 문장마다 hook 코드와 대조했고(나머지는 맞다고 확인), 고친 문장은 테스트로 코드와 함께 고정한다.
- 5단계는 계획이 `done`이 된 뒤의 일이라 계획서에 결과를 적지 못한다 → 최종 보고에 적는다.
- `settings.json` 변경이 함께 나간다 → JSON 파싱과 게이트 통과를 이미 확인했다. 되돌리기: 터미널에서 `git -C ~/.claude checkout 9be3394 -- settings.json`.

## 영향 범위
규칙 파일의 한 항목, 일관성 테스트. 릴리스되면 새 세션부터 규칙과 `CLAUDE_STATE_TTL_DAYS=95`가 적용된다.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-25 단계 1~4: 계획 저장, 닫힌 계획 `2026-11-19-guard-b15.md`를 `archive/`로 옮김(최상위 10개). `tests/test_docs_consistency.py`: D12에 `blocked` 문장 검사(규칙의 두 구절, 옛 구절이 없음, `approvals.py`가 `in-progress`가 아닌 status에 `revoke=True`를 돌려줌)를 먼저 써서 옛 문장에서 실패함을 확인, 언어 규칙 판별을 머리말 블록 안의 `paths:`로 한정, D13의 문서 대조를 숫자 전체 일치로. `rules/plan-approval.md`의 다섯째 항목을 계획의 글 그대로 고침. 게이트 45개 모듈, 2637개 검사, `GATE PASSED`. 계획대로. 5단계(리뷰, 커밋, 릴리스, push, CI)의 결과는 이 파일에 적지 못한다(`done` 뒤의 일): 최종 보고에 적는다.
