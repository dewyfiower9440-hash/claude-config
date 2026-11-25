---
status: done
task: distill-skill-wording
---

# `/distill` 스킬의 글 다섯 군데와 규칙의 예외 문장 고치기, 그 뒤 릴리스

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-25-distill-skill-wording.md`
- 배경: `distill-skill` 계획의 구현과 시험 실행은 끝났고 게이트를 통과했다(45개 모듈, 2640개 검사). 커밋 전 코드 리뷰가 fix-first로 판정했다: 사용자가 승인한 스킬의 글에 실제와 다르거나 불명확한 곳이 있다. 승인받은 글을 바꾸는 일이고 그 계획은 이미 `done`이라, 고칠 글을 다시 승인받는다. 변경은 아직 커밋도 반영도 되지 않았다(시험으로 쓴 노트 셋은 vault에 커밋돼 있다: `742de7d`).
- push: 사용자 결정 인용(2026-11-25) "push 여부 … 전부 승인"에 따라, 이 릴리스도 merge 뒤 push하고 CI를 끝까지 따라간다.

## 목표
리뷰가 확인한 다섯 군데와 규칙의 예외 문장을 고치고, 시험 노트를 고친 글에 맞춘 뒤, `distill-skill`의 변경과 함께 커밋·릴리스·push한다. 비목표: 스킬의 구조나 다른 문장 변경, hook 코드 변경, vault 도구 변경(`check`가 상대 경로를 받게 하는 것 포함).

## 조사 결과 (리뷰어가 읽거나 실행해 확인했다고 보고한 것)
- `vault-sync check`에 다른 폴더에서 상대 경로를 주면 "not a file of the vault"로 종료 코드 1이다. 절대 경로는 된다. 스킬은 이를 말하지 않고, 이 실패는 "걸린 줄이 있음"과 구분되지 않는다.
- "가장 최근 `.jsonl`"은 믿을 수 없다: `~/.claude/projects/C--dev--wt-claude-config/`에 기록이 5개 있고 가장 최근 것은 다른 세션이다.
- 틀의 `found: {{date}}`·`decided: {{date}}`는 문제를 찾은 날·결정한 날이다. 스킬의 "`{{date}}` (today)"는 지난 일을 정리할 때 틀린 날짜를 만든다(시험에서 실제로 헤맨 곳). `status`(`open`, `decided`)를 어떻게 둘지 스킬이 말하지 않는다.
- 세션 노트 이름의 날짜는 세션을 시작한 날이다(`C:\dev\vault\src\vault_sync\render.py`의 `note_relpath`). 스킬은 이를 말하지 않아 "아직 노트가 없을 때의 이름"을 오늘 날짜로 쓰기 쉽다.
- "needs no plan"은 관찰 모드에서만 참이다. hook은 이 스킬에 예외를 주지 않는다: 강제 모드(`CLAUDE_AUTHZ_MODE=enforce`)에서는 범위 밖 편집이 거부되고(`hooks/discipline.py`의 `authz_verdict`), 계획이 없는 세션도 강제 모드에서는 첫 파일부터 Stop에서 보고된다(`_scope_decision`).
- 규칙의 예외 문장이 "a skill the user invokes"로 일반화돼 있어, 다른 스킬이 자기 글에 경로를 적으면 면허로 읽힐 수 있다.
- 낮은 등급: `disable-model-invocation: true`이면 설명의 "Use when the user says …"는 모델이 쓰지 않는 문구다(사용자가 목록에서 읽는 설명으로는 남는다: 그대로 둔다).

## 변경 범위
- C:/dev/_wt/claude-config/skills/distill/**
- C:/dev/_wt/claude-config/rules/plan-approval.md
- C:/dev/_wt/claude-config/tests/test_docs_consistency.py
- C:/dev/_wt/claude-config/docs/plans/**
- C:/dev/vault/notes/knowledge/**

## 가정
- 이미 만들어 둔 미커밋 변경(`skills/distill/SKILL.md`, `skills/SKILLS.md`, `rules/plan-approval.md`, `tests/test_docs_consistency.py`, 닫힌 `distill-skill` 계획서, 계획 파일 보관 이동)은 승인받은 `distill-skill` 계획의 결과다. 이 계획은 그 위에 아래의 글만 고치고 함께 커밋한다.
- 편집과 계획서 기록을 모두 끝낸 뒤에 리뷰어를 돌린다. 리뷰가 또 글을 고쳐야 한다고 하면 고치지 않고 멈춰서 보고한다(같은 일을 세 번째로 되풀이하지 않고 방향을 사용자와 정한다).

## 선택한 접근
글을 사실에 맞게 고친다. `check`가 상대 경로를 받게 vault 도구를 고치는 안은 다른 저장소의 코드 변경이라 이 계획에 넣지 않는다(스킬이 절대 경로를 쓰면 된다).

## 고칠 글
### (1) `skills/distill/SKILL.md` 머리 문단의 마지막 문장
지금:
```markdown
Invoking this skill is the user's instruction to write these notes: it needs no plan of its
own, and it writes nothing else outside the session's project.
```
고친 뒤:
```markdown
Invoking this skill is the user's instruction to write these notes, and nothing else outside
the session's project. The hooks make no exception for it: see section 4 for what they say.
```

### (2) 2절의 새 노트 항목 (날짜와 상태)
지금:
```markdown
- Otherwise a new note from `templates/problem.md` or `templates/decision.md`: copy the
  template, fill `{{title}}` and `{{date}}` (today, `YYYY-MM-DD`), set `area`, keep the
  section headings and delete the `<!-- ... -->` hints. File name: a short Korean sentence
  that says the problem (`커밋 리뷰 차단이 재시도로 우회됨.md`), no date in the name.
```
고친 뒤:
```markdown
- Otherwise a new note from `templates/problem.md` or `templates/decision.md`: copy the
  template, fill `{{title}}`, and `{{date}}` with the day the problem was found or the
  decision made (`YYYY-MM-DD`; not today when the note is about an earlier day), set
  `area`, keep the section headings and delete the `<!-- ... -->` hints. `status` of a
  problem: `open` until its patch is verified, then `resolved`; a decision keeps `decided`.
  File name: a short Korean sentence that says the problem
  (`커밋 리뷰 차단이 재시도로 우회됨.md`), no date in the name.
```

### (3) 3절의 세션 링크 항목
지금:
```markdown
- Link the session note: its name is `YYYY-MM-DD-<first 8 characters of the session id>`,
  under `C:\dev\vault\notes\sessions\YYYY\MM\` (Glob `**/*-<first 8>.md`); write
  `[[YYYY-MM-DD-xxxxxxxx]]`. No such note yet (a session younger than 10 minutes): write the
  link by its expected name. `${CLAUDE_SESSION_ID}` printed above as a literal placeholder:
  take the id from the newest `.jsonl` under `~/.claude/projects/<this project's folder>/`.
```
고친 뒤:
```markdown
- Link the session note: its name is `<the day the session started>-<first 8 characters of
  the session id>` under `C:\dev\vault\notes\sessions\YYYY\MM\`. Find it, do not build it:
  Glob `C:/dev/vault/notes/sessions/**/*-<first 8>.md` and write the name found as
  `[[YYYY-MM-DD-xxxxxxxx]]`. Nothing found (a session younger than 10 minutes, or
  `${CLAUDE_SESSION_ID}` printed above as a literal placeholder): leave the link out and
  say so in the report. Do not guess the session from the newest transcript: several
  sessions share a project folder.
```

### (4) 4절의 첫 항목 (절대 경로)
지금:
```markdown
- `uv run --project C:/dev/vault vault-sync check <each file written>`: exit 0. A line is
  reported: mask or remove it and run again (until it passes, the vault commits nothing).
```
고친 뒤:
```markdown
- `uv run --project C:/dev/vault vault-sync check <each file written>`, each file by its
  absolute path (a relative path is refused as "not a file of the vault" from any other
  folder): exit 0. A `path:line` is reported: mask or remove that line and run again (until
  it passes, the vault commits nothing).
```

### (5) 4절의 셋째 항목 (hook이 하는 말)
지금:
```markdown
- The session holds an approved plan for its project: these notes are outside that plan,
  so the Stop reports them once as out of scope. Say in the report that they are `/distill`
  notes; do not revert them.
```
고친 뒤:
```markdown
- What the hooks say about these notes, and what to do: with an approved plan held for the
  session's project they are outside its scope, and the Stop reports them once; without a
  plan nothing is said for up to five files. Say in the report that they are `/distill`
  notes and do not revert them. Under `CLAUDE_AUTHZ_MODE=enforce` the write itself is
  refused: do not work around it, tell the user that the notes need a session started in
  `C:\dev\vault` with a plan there.
```

### (6) `rules/plan-approval.md`의 예외 문장
지금:
```markdown
  One exception: a skill the user invokes may write what its own text names outside the
  session's project (`/distill`: notes under `C:\dev\vault\notes\knowledge`).
```
고친 뒤:
```markdown
  One exception, and only this one: `/distill`, run by the user, writes its notes under
  `C:\dev\vault\notes\knowledge`. The hook makes no exception and may report the write.
```

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-25-distill-skill-wording.md`에 저장(`status: in-progress`). 최상위 계획 파일이 10개를 넘으므로 닫힌 계획 `2026-11-20-devenv-v1-1-closeout.md`를 `archive/`로 옮긴다 — 검증: 승인본과 status 줄만 다름.
- [x] 2. 테스트 먼저(`tests/test_docs_consistency.py`의 D14): 고친 뒤의 구절들("each file by its\n  absolute path", "Find it, do not build it", "Do not guess the session from the newest transcript", "`status` of a\n  problem", "The hooks make no exception for it", "One exception, and only this one")이 있고 옛 구절("needs no plan", "newest `.jsonl`", "(today,", "a skill the user invokes may write")이 없다 — 검증: 지금 글에서 실패함을 확인.
- [x] 3. 위 여섯 군데를 그대로 고친다 — 검증: `python -I tests/test_docs_consistency.py` 통과.
- [x] 4. 시험 노트 셋을 고친 글에 맞춘다: `found`를 문제를 찾은 날(2026-11-24)로 되돌린다. `uv run --project C:/dev/vault vault-sync check <절대 경로 셋>` 종료 코드 0, vault 게이트 통과 — 검증: 명령 출력.
- [x] 5. 게이트 `python -I tests/run_all.py`. 진행 기록을 쓰고 마지막 편집으로 `status: done` — 검증: `GATE PASSED`.
- [ ] 6. `code-reviewer`에게 미커밋 변경을 보임 → ship이면 커밋(스킬·등록·규칙·테스트 / 계획서 둘과 보관 이동) → 릴리스(`merge --ff-only`, 실제 hook 확인, `~/.claude`에서 게이트) → push → CI 확인 — 검증: CI run id와 결과.

## 리스크와 대응
- 글을 고칠수록 길어진다 → 여섯 군데만, 각 항목 안에서 고친다. 스킬 전체는 60줄 안팎으로 남는다.
- 강제 모드에서의 동작("the write itself is refused")은 코드의 주석과 판정 함수를 읽은 것이고 실행으로 확인한 것은 아니다 → 6단계의 리뷰어에게 이 문장을 hook 코드와 대조하게 한다.
- `status: done` 뒤의 6단계는 계획서에 적지 못한다 → 최종 보고에 적는다.

## 영향 범위
스킬 문서의 다섯 항목, 규칙의 한 문장, 일관성 테스트, vault의 시험 노트 셋의 `found` 값.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-25 단계 1~5: 계획 저장, 닫힌 계획 `2026-11-20-devenv-v1-1-closeout.md`를 `archive/`로 옮김. `tests/test_docs_consistency.py`의 D14를 고친 뒤의 구절과 없어야 할 옛 구절로 먼저 바꿔 지금 글에서 실패함을 확인한 뒤, 스킬의 다섯 군데와 규칙의 예외 문장을 계획의 글 그대로 고침(스킬은 72줄). 시험 노트 셋의 `found`를 2026-11-24로 되돌림. `vault-sync check`에 절대 경로 셋 → 종료 코드 0, 다른 폴더에서 상대 경로 → "not a file of the vault"로 거부됨을 직접 확인. vault 게이트 412 passed. 게이트 `python -I tests/run_all.py` → 45개 모듈, 2641개 검사, `GATE PASSED`. 계획대로. 6단계(리뷰, 커밋, 릴리스, push, CI)는 `done` 뒤의 일이라 최종 보고에 적는다.
