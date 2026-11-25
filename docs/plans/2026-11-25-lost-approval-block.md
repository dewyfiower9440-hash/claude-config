---
status: done
task: lost-approval-block
---

# 승인이 끊긴 상태의 편집 차단: 리뷰 뒤 고친 계획

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-25-lost-approval-block.md` (같은 파일을 이 글로 바꾼다. 같은 작업의 계획을 리뷰 결과에 맞춰 고친 것이다.)
- 지금 상태: 처음 계획의 1~5단계를 구현했고 게이트를 통과했다(45개 모듈, 2595개 검사). 커밋하지 않았다. 코드 리뷰(fix-first)와 보안 점검(PASS WITH FIXES)이 "차단이 걸리지 않는 경우"와 "세션이 혼자 차단을 푸는 길"을 찾았다. 그 수정이 처음 계획의 설계를 바꾸므로 다시 승인받는다. 문서 정정(`hooks/retention.md`)과 규칙 초안(`rules/drafts/plan-approval.md`)은 리뷰에서 문제없음으로 확인돼 그대로 둔다.
- 제약(요청 그대로): 원격·push 금지. 작업 트리의 커밋까지만 한다. `~/.claude`로의 merge는 이 계획에 없다.

## 목표
세션이 계획 승인을 한 번 가진 뒤에는, 유효한 승인이 없는 동안 그 저장소의 파일 수정을 hook이 막는다. 승인이 어떤 경로로 없어졌든 같다. 풀 수 있는 것은 사용자뿐이다. 그리고 승인을 잇는 프롬프트의 조건을 조인다(사용자 결정, 2026-11-25: "첫 줄이 `task:<id>`로 시작할 때만").

비목표: `CLAUDE_AUTHZ_MODE`를 `enforce`로 바꾸기, `settings.json` 변경, merge·push, 승인 저장 방식(v2) 변경, 셸 명령의 쓰기 대상 해석기(`hooks/shellwrites.py`) 개선.

수용 기준:
1. 승인을 가졌던 세션은 승인이 없는 동안 첫 파일부터 거부된다. 승인이 없어진 경로가 프롬프트(작업을 이름하지 않음, "승인"·"approved" 포함), 턴 끝의 무효 판정(`plan_changed` 등), 프롬프트 hook의 실패, 알림(relay) 어느 것이든 같다.
2. 차단이 풀리는 경우는 넷뿐이다: 첫 줄이 `task:<id>`로 시작하는 프롬프트로 재무장, 새 계획의 ExitPlanMode 승인, 승인이 유효한 동안 계획이 `done`으로 닫혀 hook이 종료를 기록한 경우, 사용자의 첫 줄 `revoke task:<id>`. 승인이 없어진 뒤에 계획 파일을 옮기거나 지우거나 `done`·`blocked`로 바꿔도 풀리지 않는다.
3. 경로를 다르게 적어도(8.3 짧은 이름, 정션 경유) 그 저장소 안의 파일이면 거부된다.
4. 차단 중에는 쓰기 대상을 읽지 못한 셸 명령(`unresolved`)과, 대상 판정이 실패한 셸 명령도 거부된다.
5. 첫 줄 중간에 작업 id가 들어 있는 프롬프트("do NOT go on with task:x yet")와 계획 파일 이름만 적은 첫 줄은 승인을 잇지 않는다. `task:x 이어서 진행해`처럼 같은 줄에 말을 붙인 것은 지금처럼 잇는다. 슬래시 명령의 인자(`/implement docs/plans/x.md`, `/implement task:x`)는 지금처럼 잇는다.
6. 승인을 가진 적이 없는 세션은 지금과 같다: 관찰 모드에서 5개까지 허용, 6번째 거부, 관찰 기록.
7. 거부 문구와 상태 파일에 프롬프트 글이나 파일 내용이 들어가지 않는다. 거부 문구는 푸는 방법으로 사용자의 행동만 말한다(스위치 이름을 세션에게 알려 주지 않는다).

## 조사 결과 (리뷰 두 건이 실험으로 확인했다고 보고한 것, 내가 다시 재현한 것은 아님: 2단계에서 테스트로 재현한다)
- 끊김을 프롬프트 진입 때만 기록한다(`note_approval_lost`, `hooks/discipline.py:3803`). 턴 끝의 무효 판정(`:5398`), `on_post`의 예외 경로(`:2226`), 프롬프트 hook 실패(`prompt_failed`, `:5536-5547`)는 `state["approval"]`만 지워서, 다음 프롬프트에는 "가졌던 승인"이 보이지 않고 차단도 없다.
- "그 작업의 진행 중 계획이 없으면 더 기다리지 않는다"(`_task_in_progress`, `:3816-3827`; `lost_approval_denial`, `:3845-3849`)는 내가 처음 계획에 없이 넣은 것이다. 세션이 `git mv`·`git rm`·스크립트로 계획을 옮기거나 닫으면 차단이 영구히 풀린다. 작업 id에 `/`·한글·끝의 `.`이 있으면 프롬프트 해석기(`TASK_REF`, `hooks/planscope.py:176`)를 거치는 비교가 실패해 첫 쓰기에서 풀린다.
- 경로 비교(`_inside`, `:4869`)는 글자만 본다: 8.3 짧은 이름과 정션 경유 경로가 통과한다.
- 셸 쓰기 판정(`shell_write_denial`, `:806-874`)에서 `paths`가 비고 `unresolved`만 참이면 통과하고, 바깥 `except`는 관찰 모드에서 None을 돌려준다.
- 재무장(변경 전부터): `naming_text`는 첫 줄 전체를 돌려주고(`planscope.py:195-207`), `prompt_tasks`는 그 줄 어디에 있든 `task:<id>`를 찾고(`:210-212`), `names_plan`은 계획 파일 이름도 받는다(`:215-232`, `named_plans` `:282-306`).
- 정상 종료의 기록: `ledger_closed`와 `already_closed`(`:3756-3765`). 사용자 명령: `revoke task:<id>`(`REVOKE_CMD`, `:2824`).
- 테스트의 빈 곳: L-T9는 2개 허용만 본다(5개 한도·6번째 거부·관찰 기록 없음). Z-T27은 상수 비교라 실패할 수 없다.
- hook 지연(리뷰어가 테스트를 돌리는 중에 잼): `pre(Edit)` +26.2%, `prompt` +17.5%, 나머지 -5.6%~+6.2%. 조용한 상태에서 다시 재야 판단할 수 있다.

## 변경 범위
- C:/dev/_wt/claude-config/hooks/discipline.py
- C:/dev/_wt/claude-config/hooks/planscope.py
- C:/dev/_wt/claude-config/hooks/retention.md
- C:/dev/_wt/claude-config/tests/**
- C:/dev/_wt/claude-config/CLAUDE.md
- C:/dev/_wt/claude-config/skills/**
- C:/dev/_wt/claude-config/rules/drafts/**
- C:/dev/_wt/claude-config/docs/plans/**

## 가정
- 재무장 규칙이 바뀌면 기존 테스트와 `tests/bench_hooks.py`의 시나리오 가운데 "첫 줄이 계획 파일을 이름함 → 재무장"을 기대하는 것들이 새 규칙에 맞게 바뀐다(동작이 바뀌므로 그 동작을 적은 테스트를 고친다. 약화가 아니라 기대값 변경이며, 바꾼 검사 이름을 보고에 적는다).
- `skills/**`는 스킬 문서가 "계획 파일을 이름하면 재무장"이라고 말하는 문장을 새 규칙에 맞추기 위해서만 범위에 넣는다. 해당 문장이 없으면 건드리지 않는다.
- 스위치 `CLAUDE_LOST_APPROVAL=notify`는 남긴다(잠겼을 때 사용자가 쓰는 출구). hook 프로세스의 환경에서만 읽으므로 명령 앞에 붙여서는 바뀌지 않는다(보안 점검이 확인). 프로젝트의 `.claude/settings.local.json`이 hook의 환경을 바꿀 수 있는지는 미확인으로 보고에 적는다.
- 리뷰 결과 보고가 새 프롬프트로 들어와 hook이 승인이 끊겼다고 알리면, 파일을 고치지 않고 사용자에게 첫 줄 `task:lost-approval-block`을 요청한다.

## 선택한 접근
| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **A (선택)** | "이 세션이 마지막으로 가졌던 승인"을 상태에 남긴다(`approval_held`: 작업 id, 저장소, 승인 id). 승인이 서 있는 모든 hook 실행의 끝에서 갱신한다. 판정은 "그 기록이 있고 지금 유효한 승인이 없으면 거부" 하나다. 지우는 곳은 넷뿐(수용 기준 2) | 승인이 없어지는 경로를 하나하나 따라갈 필요가 없다: 새 경로가 생겨도 빠지지 않는다. 계획 파일을 보지 않으므로 파일 조작으로 풀 수 없다 | 한 번 승인을 가진 세션은 그 뒤 사용자 행동 없이는 그 저장소를 못 고친다(의도한 것) |
| B | 지금 구조를 유지하고 승인을 지우는 곳마다 끊김을 기록 | 변경이 작다 | 리뷰가 이미 세 곳을 놓쳤음을 보였다. 다음에 또 놓친다 |
| C | 끊긴 뒤에는 계획 파일 상태를 계속 읽어 판정 | 계획이 실제로 끝났는지 반영 | 세션이 계획 파일을 고쳐 차단을 푼다(지금의 결함) |
A를 고른 이유: 실패하는 방향이 "막힘"이고, 풀 수 있는 주체가 사용자로 한정된다.

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-25-lost-approval-block.md`에 저장(`status: in-progress`, 진행 기록의 기존 두 줄은 유지) — 검증: 승인본과 status 줄·진행 기록만 다름.
- [x] 2. 테스트 먼저, 리뷰가 보고한 경우를 재현(`tests/test_prompt_approval.py`, `tests/test_authz.py`): (a) 승인 → 계획의 변경 범위를 고침 → Stop → 프롬프트 "approved" → 지금은 허용됨을 확인. (b) 프롬프트 hook 실패 뒤 → 허용됨. (c) 끊긴 뒤 계획을 `git mv`·삭제·`done`·`blocked`로 → 허용됨. (d) 작업 id `feat/x`, `v1.`, 한글 → 허용됨. (e) 8.3 짧은 이름, 정션 경유 경로 → 허용됨. (f) `F=src/x.py; echo x > $F`와 대상 판정 예외 → 허용됨. (g) 재무장: 첫 줄 중간의 `task:x`, 계획 파일 이름만 적은 첫 줄 → 지금은 재무장됨; `task:x 이어서`, 슬래시 명령 인자 → 재무장. (h) L-T9를 5개 허용·6번째 거부·관찰 기록까지 보도록 넓히고, Z-T27을 동작 검사로 바꾼다. (i) 승인이 유효한 동안 `done`으로 닫은 뒤 → 거부 없음. (j) `revoke task:<id>` → 거부 없음. (k) 다른 저장소의 파일 → 거부 없음 — 검증: (a)~(g)의 새 검사가 기대한 이유로 실패하고, 기존 L-T·Z-T 가운데 설계가 바뀌는 것(L-T11, Z-T25)이 무엇인지 목록으로 적는다.
- [x] 3. `hooks/discipline.py`: `approval_lost`·`note_approval_lost`·`_task_in_progress`를 걷어내고 `approval_held`로 바꾼다. 갱신은 프롬프트·post·stop 처리의 끝에서(승인이 서 있으면 기록, 승인이 유효한 동안의 `done` 종료가 기록되면 지움), `revoke task:`가 그 작업의 기록을 지운다. `lost_approval_denial`은 계획 파일을 읽지 않는다. 경로는 글자 비교와 `os.path.realpath` 비교 둘 다로 저장소 안인지 본다(정션·짧은 이름). 저장소를 모르는 기록이면 모든 경로를 거부한다. 셸: 차단 중이면 `unresolved`와 대상 판정 예외도 거부. 판정 예외 때의 문구에 작업 id를 넣는다. `LOST_NOTE`는 스위치가 `notify`일 때 "거부됩니다"라고 말하지 않는다. 거부 문구에서 스위치 안내를 뺀다 — 검증: 2단계의 검사 통과, `python -I tests/test_authz.py`·`test_prompt_approval.py`·`test_task_switch.py`·`test_approvals_v2.py`·`test_ledger.py` 통과.
- [x] 4. `hooks/planscope.py`: 보통 프롬프트의 첫 줄은 `task:<id>`(또는 `task_id:`·`task=`)로 시작할 때만 작업을 이름한다. 계획 파일 이름은 슬래시 명령의 인자에서만 받는다. `revoke task:`, `recover-state`, `ack-missing` 같은 첫 줄 명령은 그대로 — 검증: 2단계 (g) 통과. 이 규칙 때문에 기대값이 바뀌는 기존 검사와 `tests/bench_hooks.py`의 시나리오를 고치고 그 목록을 진행 기록에 적는다.
- [x] 5. 문서: `CLAUDE.md`의 문장을 새 동작에 맞춘다(첫 줄이 `task:<id>`로 시작, 풀리는 경우 넷, 스위치는 사용자의 것). `skills/plan/SKILL.md`·`skills/implement/SKILL.md`에 "계획 파일을 이름하면 재무장"이라는 말이 있으면 고친다. `rules/drafts/plan-approval.md`의 마지막 항목이 "계획을 닫으면 차단이 풀린다"로 읽히지 않게 고친다. `tests/test_docs_consistency.py`의 문장 검사 갱신 — 검증: `python -I tests/test_docs_consistency.py` 통과, `CLAUDE.md` 100줄 이내.
- [x] 6. 게이트 `python -I tests/run_all.py` → 고친 부분을 `code-reviewer`와 `security-auditor`에게 다시 보임 → 확인된 지적 수정 → 게이트. 리뷰어가 끝난 조용한 상태에서 `tests/bench_hooks.py`로 지연을 다시 잰다(같은 조건에서 변경 전 커밋도 한 번 재서 비교한다: 임시 작업 트리 `git worktree add`를 `C:\dev\_wt` 아래에 만들고 끝나면 지운다. 측정 파일은 커밋하지 않는다). 20% 넘게 느려진 hook이 있으면 원인을 찾고, 못 찾으면 멈추고 보고한다 — 검증: `GATE PASSED`, 지연 표.
- [x] 7. 커밋(논리 단위: 차단, 재무장 규칙, 문서·규칙 초안·계획 보관). 차단 동작 재현 출력을 보고용으로 모으고, 진행 기록을 쓴 뒤 마지막 편집으로 `status: done`, 커밋 — 검증: `git status --short` 비어 있음.

## 리스크와 대응
- 재무장 규칙 변경이 여러 테스트 모듈에 번진다 → 4단계에서 실패 목록을 먼저 뽑고, 기대값이 바뀌는 것과 실제 결함을 가른다. 같은 게이트가 두 번 실패하면 멈추고 보고한다.
- 잠김: 한 번 승인을 가진 세션은 사용자 행동 없이는 그 저장소를 못 고친다 → 출구는 `task:` 줄, 새 계획 승인, `revoke task:<id>`, 스위치. 거부 문구가 앞의 셋을 말한다. 작업 트리의 변경은 merge 전에는 실제 세션에 적용되지 않는다.
- 닫힌 계획 뒤의 일상 작업: 계획을 `done`으로 닫아 승인이 정상 종료되면 기록이 지워지므로 그 뒤의 작은 수정은 지금처럼 된다. 테스트 (i)로 고정한다.
- 차단 중 `unresolved` 셸 명령 거부가 읽기 전용 명령까지 막을 수 있다 → `unresolved`가 어떤 명령에서 참이 되는지 2단계에서 실제 명령 몇 개(`git status`, `pytest`, 파이프, 변수 리다이렉트)로 확인하고, 읽기 전용 명령이 막히면 멈추고 보고한다.
- `realpath`는 파일 시스템을 읽는다(지연, 없는 공유 폴더) → UNC 경로는 기존 코드처럼 해석하지 않고, 차단 중일 때만 호출한다. 6단계에서 잰다.
- 이 작업 자체가 같은 함정에 빠진다 → 가정의 마지막 항목대로 한다.
- 되돌리기: 커밋을 `git revert`.

## 영향 범위
`hooks/discipline.py`(상태 키 `approval_held`, 편집·셸 판정, 알림 문구), `hooks/planscope.py`(재무장 규칙: 기존 동작이 바뀐다), 환경 변수 `CLAUDE_LOST_APPROVAL`, `CLAUDE.md` 한 문장, 스킬 문서의 해당 문장, 테스트 여러 모듈, `tests/bench_hooks.py`의 시나리오. 실제 세션에는 merge 뒤에 적용된다(이 계획 밖).

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-25 처음 계획의 1~5단계: `tests/test_prompt_approval.py`에 L-T1~L-T14, `tests/test_authz.py`에 Z-T22~Z-T27, `hooks/discipline.py`에 `approval_lost` 기반 차단, `CLAUDE.md` 한 문장과 그 검사, `hooks/retention.md` 정정, `rules/drafts/plan-approval.md` 초안. 계획과 달랐던 점: `docs/plans`의 파일이 11개가 되어 D10 검사가 실패 → 관례대로 가장 오래된 닫힌 계획 `2026-11-18-devenv-state.md`를 `docs/plans/archive/`로 옮김(`git mv`). 계획에 없던 "끊긴 뒤 계획이 닫히면 더 기다리지 않는다"를 넣음(이것이 리뷰에서 결함으로 판정됨).
- 2026-11-25 처음 계획의 6단계: 게이트 45개 모듈, 2595개 검사, `GATE PASSED`. 코드 리뷰 fix-first, 보안 점검 PASS WITH FIXES → 설계를 바꾼 이 계획으로 다시 승인받음. 사용자 결정: 재무장은 "첫 줄이 `task:<id>`로 시작할 때만".
- 2026-11-25 단계 1~5(고친 계획): `hooks/discipline.py`를 `approval_held` 설계로 바꿈(`sync_approval_held`를 모든 hook 실행의 저장 직전에 호출, `forget_approval_held`는 승인이 서 있는 동안의 `done` 종료와 `revoke task:` 두 곳에서만, `lost_approval_denial`은 계획 파일을 읽지 않음, `_in_repository`는 글자 비교와 `realpath` 비교, 셸은 `unresolved`와 판정 예외도 거부, `LOST_NOTE_NOTIFY`, 거부 문구에서 스위치 안내 제거). `hooks/planscope.py`: `leading_task`, `named_plans`가 보통 프롬프트에서는 첫 줄이 작업으로 시작할 때만 이름으로 인정하고 계획 파일 이름은 슬래시 명령에서만 받음. 테스트: `tests/test_prompt_approval.py` L-T9b~L-T27, `tests/test_authz.py` Z-T22~Z-T28, `tests/test_docs_consistency.py` D4. `CLAUDE.md` 문장, `skills/plan/SKILL.md` 한 문장, `rules/drafts/plan-approval.md` 두 항목. 계획과 다른 점: (1) 2단계의 "테스트 먼저"를 지키지 못함: 차단 쪽은 구현을 먼저 바꾸고 테스트를 썼다(실패하는 모습은 리뷰어 두 명의 실험 보고로만 있다). 재무장 규칙(L-T26)은 구현 전에 실패를 확인했다(8개 입력 중 6개가 재무장됨). (2) 직접 친 슬래시 명령(`/implement docs/plans/x.md`, 머리말 태그 없이 `/`로 시작하는 첫 줄)도 슬래시 명령으로 다룸: 기존 테스트와 벤치마크 시나리오 N2가 이 형태를 쓴다. 그래서 `tests/bench_hooks.py`는 고치지 않았다. (3) 첫 줄이 작업으로 시작하면 그 줄에 적힌 작업 전부를 이름으로 본다(둘이면 전처럼 "여러 개"로 재무장하지 않음). (4) 기대값이 바뀐 기존 검사는 `tests/test_approvals.py`의 P0-03 넷(문장 끝 마침표·`@` 언급·한글 바로 앞·하위 폴더 상대 경로의 계획 파일을 보통 첫 줄에 적은 경우: 재무장 → 재무장 안 됨). 같은 네 형태를 슬래시 명령 인자로 옮긴 검사를 추가해 경로 해석의 검사는 남겼다. (5) `revoke task:`의 작업 id는 ASCII만 받는다(기존 정규식): 한글 id의 작업은 revoke로 내려놓을 수 없고 새 계획 승인이나 스위치로만 풀린다.
- 2026-11-25 단계 6(게이트와 지연): `python -I tests/run_all.py` → 45개 모듈, 2622개 검사, `GATE PASSED`. hook 지연은 변경 전 커밋(임시 작업 트리)과 번갈아 두 번씩 재서 가장 빠른 값끼리 비교: post(Edit) -4.5%, pre(Edit) -6.2%, prompt -16.1%, stop -18.7%, format_lint -5.0%, guard -9.5%, session_context -8.1%(느려진 것 없음, 차이는 측정 오차로 본다). 승인 시나리오 13건의 결과는 같다. 리뷰어가 테스트를 돌리던 때의 +26%는 부하였다. 측정 파일과 임시 작업 트리는 지웠다. 작업 트리의 `docs/plans/2026-11-24-monitor-v1-2.md` 변경(10:45, 진행 기록 한 줄)은 다른 세션의 것이라 건드리지 않았다.
- 2026-11-25 단계 6(리뷰 2차): 코드 리뷰 fix-first, 보안 점검 PASS WITH FIXES. 이번에는 테스트를 먼저 써서 실패를 확인한 뒤(L-T28~L-T32, 5묶음 모두 보고된 대로 실패) 고쳤다. (1) 계획을 `done`으로 닫는 정상 흐름이 다음 프롬프트를 잠금: `v2_invalid`가 기록을 지워도 같은 실행 끝의 `sync_approval_held`가 다시 씀 → `plan_status:done`으로 끝난 프롬프트에서는 기록하지 않고, 프롬프트 진입 때 들어오는 승인을 한 번 더 검사해(`v2_write_check`) 어떤 hook도 닫힌 계획을 보지 못한 채 중단된 경우도 정상 종료로 기록한다. (2) 이전 저장 방식(v1)의 승인은 지우는 곳이 없어 기록하지 않는다(v1에서는 이 차단이 없다: 변경 전과 같다). (3) `recover-state`와 상태 복구가 기록을 지우던 것: `recovery_carry`가 옮긴다. (4) `\\?\`·`\\.\` 접두 경로와 공유 경로: 접두를 벗겨 비교하고, 볼륨·개체 이름과 그 밖의 공유 경로는 차단 중 "안"으로 본다. (5) `/`로 시작하기만 한 첫 줄(`//주석`, `/tmp/x 경로`)이 슬래시 명령으로 취급되던 것: `TYPED_COMMAND`(슬래시, 명령 단어, 공백이나 끝). (6) 승인이 서 있는데 다른 프롬프트 id를 싣거나 싣지 않은 도구 호출(하위 에이전트)이 거부될 수 있던 것: 상태가 기록한 진행 중 프롬프트의 승인도 "서 있음"으로 본다. 테스트: L-T28~L-T32, Z-T29~Z-T33. 게이트 45개 모듈, 2632개 검사, `GATE PASSED`. 고치지 않고 남긴 것: 사용자가 직접 `blocked`로 바꾼 계획은 `task:` 줄로 이어지지 않고 `in-progress`로 되돌린 뒤의 `task:` 줄이나 `revoke task:`로만 풀린다(초안 규칙에 적음). 세션 상태 파일이 지워지거나 깨지면 기록도 없어진다. 프롬프트 hook이 돌지 않은 턴에 계획을 닫고 Stop하면 기록이 지워진다. `- task:x`, `> task:x`, `네, task:x`처럼 작업으로 시작하지 않는 첫 줄은 재무장하지 않는다(결정대로). hook이 쓰기로 보지 못하는 셸 명령(`python gen.py`, `python -c`, `git apply`·`checkout`·`mv`·`stash pop`, `perl -pi`, `ruff format`, `curl -o`, `bash -c`, PowerShell `[IO.File]::WriteAllText`·`iex` 등)은 차단 중에도 통과한다(쓰기 대상 해석기의 한계, 이 계획의 비목표). 하위 에이전트의 hook이 어떤 프롬프트 id를 싣는지, 예약 프롬프트와 `claude -p --resume`로 세션이 스스로 평문 첫 줄을 만들 수 있는지는 미확인.
- 2026-11-25 단계 6~7(마감): 고친 부분의 3차 코드 리뷰 판정 ship(첫 실행은 API 529로 끊겨 다시 맡김). 커밋 `b62d947`(차단과 재무장 규칙: `hooks/discipline.py`, `hooks/planscope.py`, 테스트 네 모듈, `CLAUDE.md`, `skills/plan/SKILL.md`), `ab62590`(`hooks/retention.md`, `rules/drafts/plan-approval.md`, 닫힌 계획 하나를 archive로). 계획과 다른 점: 7단계의 "차단"과 "재무장 규칙"을 따로 커밋하지 못했다(같은 테스트 파일과 같은 `CLAUDE.md` 문장에 걸쳐 있어 한 커밋으로 묶음). 리뷰 뒤 `tests/test_prompt_approval.py`를 한 번 더 실행해 `FAILURES: 0`을 확인(L-T1, L-T6, L-T8, L-T9b, L-T11, L-T15, L-T16, L-T20, L-T22, L-T26, L-T28 출력을 보고에 넣음). 3차 리뷰가 남긴 낮은 등급 둘은 고치지 않았다: (1) 계획이 `done`이 아닌 채 바뀐 것을 어떤 hook도 보지 못한 상태에서 작업을 이름하지 않은 프롬프트가 오면, 프롬프트 진입 때의 재검사가 그 승인을 저장소에서 REVOKED로 만든다(전에는 그 프롬프트에서는 저장소가 그대로였고, 계획을 되돌린 뒤 `task:` 줄로 이어 갈 수 있었다). 이제는 새 승인이나 revoke만 남는다. (2) "상태가 기록한 진행 중 프롬프트의 승인도 서 있음으로 본다"는 것은 프롬프트 hook이 아예 돌지 않은 새 프롬프트에서 예전 승인이 남아 있을 때 거부하지 않는다는 뜻이기도 하다(하위 에이전트의 호출과 구분할 수 없어서 생긴 절충, 변경 전보다 나빠지지는 않음). 검증 상태: Locally Verified, CI 미검증(push 전: 사용자 제약 "원격·push 금지"). `~/.claude`에는 반영하지 않았다.
