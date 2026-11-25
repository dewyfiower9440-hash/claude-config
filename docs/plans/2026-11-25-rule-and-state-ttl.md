---
status: done
task: rule-and-state-ttl
---

# 계획 승인 규칙 적용과 세션 상태 보존 기간 설정

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-25-rule-and-state-ttl.md`
- 배경: 사용자가 2026-11-25에 세 가지를 승인했다: "push 여부, 규칙 초안 적용 여부, CLAUDE_STATE_TTL_DAYS 설정 전부 승인". push는 이미 했다(`aebafd0..9be3394`, CI run 45565361591 성공). 남은 둘이 이 계획이다.
- push: 위 결정에 따라 이 작업의 릴리스도 merge 뒤 push하고 CI를 끝까지 따라간다(사용자 결정 인용: "push 여부 … 전부 승인").

## 목표
1. 규칙 초안(`docs/plans/archive/2026-11-25-draft-rule-plan-approval.md`)을 전역 규칙으로 적용한다: `rules/plan-approval.md`.
2. `settings.json`의 `env`에 `CLAUDE_STATE_TTL_DAYS`를 넣어, 세션 상태가 대화 기록(90일)보다 먼저 지워지지 않게 한다.

비목표: hook 코드 변경, `cleanupPeriodDays` 변경, `CLAUDE.md` 본문 변경(100줄 상한에 닿아 있다), 다른 세션의 미커밋 파일(`docs/plans/2026-11-24-monitor-v1-2.md`).

수용 기준:
- `rules/plan-approval.md`가 있고 `paths:` 머리말이 없다(모든 세션에 읽힌다). "초안" 표시가 없다. 내용이 지금의 hook 동작과 어긋나지 않는다.
- `tests/test_docs_consistency.py`가 언어 규칙(머리말에 `paths:`가 있는 파일)에만 "Quality gate" 검사를 하고, 새 규칙의 핵심 문장 둘을 hook의 동작과 함께 고정한다.
- `settings.json`의 `env.CLAUDE_STATE_TTL_DAYS`가 `cleanupPeriodDays`보다 크다는 것을 테스트가 확인한다(둘 중 하나만 바뀌어 다시 어긋나면 게이트가 실패한다).
- `hooks/retention.md`가 새 값을 말한다.

## 조사 결과
- Claude Code는 `rules/` 아래의 `.md`를 하위 폴더까지 읽고, `paths:`가 없는 규칙은 시작할 때 모든 세션에 로드한다(공식 문서 memory 절, 2026-11-25 확인. 사용자 폴더 `~/.claude/rules/`에 대해 "재귀"라는 말은 그 페이지에 따로 없다).
- `tests/test_docs_consistency.py:46-48`은 `rules/*.md` 전부를 `language_rules`로 읽고 `:81`에서 모두에 "Quality gate"가 있는지 본다. 지금의 세 파일(`python.md`, `typescript.md`, `powershell.md`)은 모두 `paths:` 머리말로 시작한다.
- `settings.json:409-413`의 `env`에는 `BASH_MAX_TIMEOUT_MS`, `CLAUDE_REVIEW_GATE`, `CLAUDE_COMMIT_REVIEW`가 있다. hook은 이 환경을 물려받는다(`CLAUDE_REVIEW_GATE`가 그렇게 쓰이고 있다). `cleanupPeriodDays`는 90(`:415`).
- `hooks/state_gc.py`: 기본 35일(`:30`), `CLAUDE_STATE_TTL_DAYS`는 1~365의 정수만 받는다(`:40-46`). 세션 종료 때 35일(설정하면 그 값) 넘게 안 쓰인 상태 파일을 한 번에 1초·2,000개까지 지운다.
- `hooks/retention.md`는 지금 "그 조건이 지켜지지 않는다(35 < 90)"고 적고 있다(이번 릴리스에서 내가 쓴 문장).

## 변경 범위
- C:/dev/_wt/claude-config/rules/plan-approval.md
- C:/dev/_wt/claude-config/docs/plans/**
- C:/dev/_wt/claude-config/tests/test_docs_consistency.py
- C:/dev/_wt/claude-config/settings.json
- C:/dev/_wt/claude-config/hooks/retention.md
- C:/dev/_wt/claude-config/tests/_baseline/**

## 가정
- 규칙은 `rules/`의 파일로 둔다. `CLAUDE.md`는 100줄 상한에 닿아 있어 넣을 자리가 없다.
- 값은 95일로 한다(대화 기록 90일보다 5일 길다. 기본값 35일이 30일보다 5일 긴 것과 같은 여유).
- 보존 기간이 35일에서 95일로 늘면 세션 상태 파일이 더 오래 남는다. 2026-11-15 기준 약 37,000개·68MB였고 대부분은 테스트가 남긴 것으로, 지금은 테스트가 전용 임시 폴더를 쓴다. 디스크 사용이 다시 문제가 되면 값을 줄이는 대신 `cleanupPeriodDays`와 함께 본다.
- `hooks/` 아래에서 바뀌는 것은 문서(`retention.md`)뿐이라 hook 지연은 달라지지 않는다. 릴리스 절차대로 한 번 재서 기준선 파일로 남긴다.
- 코드 파일은 테스트 하나만 바뀐다. 커밋 hook이 리뷰를 요구하면 `code-reviewer`에게 diff를 보인다. 그 결과 보고로 승인이 끊겼다고 hook이 알리면(이제 실제로 수정이 거부된다) 사용자에게 `task:rule-and-state-ttl`로 시작하는 첫 줄을 요청한다.

## 선택한 접근
| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **A (선택)** | `rules/plan-approval.md`(머리말 없음) + 일관성 테스트가 언어 규칙을 `paths:` 머리말로 가름 | `CLAUDE.md`를 건드리지 않는다. 규칙이 한 파일에 모인다 | 모든 세션의 컨텍스트에 약 25줄이 더 실린다 |
| B | `CLAUDE.md` 2절에 한두 문장으로 | 파일이 늘지 않는다 | 100줄 상한: 다른 문장을 줄여야 하고, 일곱 항목을 두 문장에 담을 수 없다 |
| C | `paths: docs/plans/**`를 달아 계획 파일을 다룰 때만 로드 | 컨텍스트 절약 | "세션을 프로젝트 루트에서 시작한다" 같은 항목은 계획 파일을 열기 전에 필요하다 |
A를 고른 이유: 이 규칙이 필요한 순간(다른 저장소로 `cd`하기 전, 승인이 끊겼다는 알림을 받았을 때)은 계획 파일을 읽는 때와 겹치지 않는다.

## 규칙의 글 (이대로 `rules/plan-approval.md`가 된다)
```markdown
# Plan approval (applies everywhere)

An approved plan is bound to one repository: the git root of the folder the session was in
when the plan was approved. The hook looks for the saved plan in that root's `docs/plans`.

- **Start the session in the project root.** An approval given from another folder is bound
  to that folder's repository, and saving the plan in the intended project is refused.
  Before presenting a plan, check where the session is (`pwd`,
  `git rev-parse --show-toplevel`) and go back to the project root first.
- **A `cd` into another repository does not take the approval along.** Edits there need that
  repository's own approved plan. For reading, prefer `git -C <path>` and absolute paths:
  the session stays where its approval is.
- **Two repositories, two plans.** A task that changes two repositories is planned and
  approved once in each, one after the other, each from its own root.
- **A message is not an approval.** "승인", "approved", "go on" in the user's message do
  not continue an approval the session no longer holds, and neither does a task id
  mentioned in the middle of a line. The hook then refuses every edit in that repository.
  Stop editing and ask the user for one of: a prompt whose first line starts with
  `task:<id>`, the plan presented again and approved (ExitPlanMode), or a first line
  `revoke task:<id>` to lay the task down. Do not look for another way to write the files.
- **What is done to the plan file does not bring an approval back.** Closing, moving or
  deleting it after the approval is gone changes nothing. A plan parked as `blocked` is
  continued only after the user sets it to `in-progress` again.
- **Closing a plan (`status: done`) ends its approval at once.** Make it the last edit of
  the task, after the progress record and Gotchas, and commit afterwards.
- **A subagent's report can end the turn's approval.** Finish the edits that need the
  approval before starting reviewers, or expect to ask for the `task:<id>` line afterwards.
```

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-25-rule-and-state-ttl.md`에 저장(`status: in-progress`) — 검증: 승인본과 status 줄만 다름. `docs/plans`의 파일이 10개를 넘으면(D10 검사) 닫힌 계획 가운데 가장 오래된 것을 `archive/`로 옮긴다.
- [x] 2. 테스트 먼저(`tests/test_docs_consistency.py`): (a) 언어 규칙은 `paths:` 머리말로 시작하는 파일로 정의하고, 그 셋의 이름을 고정한다. (b) `rules/plan-approval.md`가 있고 `paths:`가 없으며 "draft"·"초안"이 없다. (c) 규칙의 두 문장("A message is not an approval", `revoke task:<id>`)이 있고, hook의 거부 문구(`REASON_LOST_APPROVAL`)가 같은 세 가지 방법을 말한다. (d) `settings.json`의 `env.CLAUDE_STATE_TTL_DAYS`가 정수이고 `cleanupPeriodDays`보다 크며 365 이하다 — 검증: `python -I tests/test_docs_consistency.py`에서 (b)(c)(d)가 기대한 이유로 실패(파일 없음, 키 없음).
- [x] 3. `rules/plan-approval.md`를 위의 글로 만들고, 초안 파일 `docs/plans/archive/2026-11-25-draft-rule-plan-approval.md`를 지운다(`git rm`: 같은 글이 규칙이 되었고 이력에 남는다) — 검증: (b)(c) 통과.
- [x] 4. `settings.json`의 `env`에 `"CLAUDE_STATE_TTL_DAYS": "95"` 추가, `hooks/retention.md`의 해당 두 문단을 새 값으로 고친다(35일이라고 적은 곳 포함) — 검증: (d) 통과, `python -I tests/test_state_gc.py` 통과, JSON이 유효함(`python -c "import json; json.load(...)"`).
- [x] 5. 게이트 `python -I tests/run_all.py` → 커밋(규칙과 테스트, 설정과 문서, 계획서). 진행 기록을 쓴 뒤 마지막 편집으로 `status: done`, 커밋 — 검증: `GATE PASSED`, 내 변경에 대해 `git status --short`가 비어 있음.
- [ ] 6. 릴리스(`/release-hooks`): 지연 측정과 기준선 커밋 → `git -C ~/.claude merge --ff-only devenv-final` → 평범한 명령으로 실제 hook 확인 → `~/.claude`에서 게이트 → push → CI를 끝까지 확인 — 검증: CI run id와 결과. 새 세션을 열어야 규칙과 환경 변수가 적용된다는 것을 보고에 적는다.

## 리스크와 대응
- `settings.json`은 실제 설정 파일이다. 잘못된 JSON이면 Claude Code가 설정을 못 읽는다 → 4단계에서 파싱해 확인하고, 게이트의 설정 관련 검사를 통과시킨 뒤에만 merge한다. 되돌리기: 터미널에서 `git -C ~/.claude checkout <이전 커밋> -- settings.json`.
- 규칙이 모든 세션에 실리므로, 틀린 문장이 있으면 모든 프로젝트에서 잘못 행동한다 → 문장을 hook의 실제 동작(테스트 L-T1~L-T32)과 대조했고, 핵심 둘은 일관성 테스트로 고정한다.
- 이 계획의 승인부터는 새 hook이 실제로 적용된다: 승인이 끊기면 이 저장소의 수정이 거부된다 → 그때는 `task:` 줄을 요청한다. 리뷰어는 편집을 모두 끝낸 뒤에 돌린다.
- `status: done`은 승인을 끝낸다 → 5단계에서 커밋 직전의 마지막 편집으로 한다. 6단계의 기준선 커밋은 릴리스 절차의 일이고 계획 파일을 건드리지 않는다. 그 커밋이 거부되면(승인이 끝난 뒤의 편집으로 판정) 멈추고 보고한다.

## 영향 범위
새 전역 규칙 파일 하나(모든 세션의 컨텍스트에 약 25줄), `settings.json`의 환경 변수 하나(세션 상태 보존 35일 → 95일), 문서 한 곳, 일관성 테스트. 새 세션부터 적용된다.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-25 단계 1~4: 계획 저장(`docs/plans`가 11개가 되어 가장 오래된 닫힌 계획 `2026-11-18-devenv-final.md`를 `archive/`로 옮김). `tests/test_docs_consistency.py`에 D12(언어 규칙은 `paths:` 머리말로 가름, 그 밖의 규칙 파일은 이름으로 나열, `rules/`의 하위 폴더에 `.md` 없음, 규칙의 핵심 문장과 hook 거부 문구의 세 가지 방법)와 D13(`CLAUDE_STATE_TTL_DAYS` > `cleanupPeriodDays`, 365 이하, `retention.md`가 두 값을 말함)을 먼저 써서 세 검사가 "파일 없음·키 없음"으로 실패하는 것을 확인. `rules/plan-approval.md`를 계획의 글 그대로 만들고 초안 파일을 `git rm`. `settings.json`의 `env`에 `CLAUDE_STATE_TTL_DAYS: "95"`(JSON 파싱 확인), `hooks/retention.md`의 네 곳을 새 값으로. 계획대로.
- 2026-11-25 단계 5: 게이트 `python -I tests/run_all.py` → 45개 모듈, 2636개 검사, `GATE PASSED`. 계획과 다른 점: 커밋 hook이 리뷰를 요구했다(세션 전체의 변경을 기준으로 stale). 리뷰어의 결과 보고가 승인을 끊으면 이 파일의 편집이 거부되므로, 이 기록과 `status: done`을 커밋보다 먼저 쓰고 그 뒤에 `code-reviewer`에게 diff를 보였다. 커밋(규칙과 테스트, 설정과 문서, 이 계획서)은 리뷰 뒤에 한다. 6단계(릴리스, push, CI)의 결과는 이 파일에 적지 못한다: 최종 보고에 적는다.
