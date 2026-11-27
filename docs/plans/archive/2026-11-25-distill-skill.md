---
status: done
task: distill-skill
---

# `/distill`: 세션에서 배운 것을 Obsidian 지식 노트로 정리하는 스킬

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-25-distill-skill.md`
- 배경: Obsidian 보관함(`C:\dev\vault\notes`)에는 세션 노트 52개와 환경 문서 사본 100개가 자동으로 쌓이지만, 직접 쓰는 `knowledge/`는 안내문 하나뿐이다. 원자료를 "문제 → 원인 → 패치" 노트로 줄이는 일이 손으로는 이어지지 않아서, 그 일을 하는 스킬을 만든다(2026-11-24에 설계만 해 둔 2부). 1부(직접 쓴 노트의 가림 검사 `vault-sync check`)는 끝나 있다.
- push: 사용자 결정 인용(2026-11-25) "push 여부 … 전부 승인"에 따라 이 릴리스도 merge 뒤 push하고 CI를 끝까지 따라간다. 작업 트리에 먼저 커밋돼 있는 릴리스 절차 문서의 두 커밋(`16a8503`, `bd76992`)이 함께 나간다.

## 목표
사용자가 `/distill`을 부르면, 그 세션에서 다시 쓸 만한 문제·결정을 골라 `C:\dev\vault\notes\knowledge`에 노트로 쓰거나 기존 노트에 덧붙이고, 가림 검사와 vault 게이트까지 끝낸 뒤 무엇을 썼는지 보고한다. 비목표: 자동 실행(세션 종료 hook), 세션 노트 자체의 요약, vault 도구(`vault_sync`) 코드 변경, hook 코드 변경.

수용 기준:
- `skills/distill/SKILL.md`가 있고 `skills/SKILLS.md`에 등록돼 있다. 사용자가 부를 때만 실행된다(`disable-model-invocation: true`).
- 스킬의 절차를 이 세션에 그대로 적용해 노트 1~3개를 실제로 만든다: 틀을 따르고, 근거(`파일:줄`, 커밋, 명령 출력)가 있고, 세션 노트 링크가 있고, `vault-sync check`가 0으로 끝나고, vault 게이트가 통과하고, 다음 예약 실행이 그 노트를 커밋한다(새 커밋 방식이 실제 보관함에서 커밋을 만드는 첫 확인이기도 하다).
- 스킬 문서가 말하는 명령과 경로가 실제와 같다(일관성 테스트로 고정: 틀 파일 이름, `vault-sync check`, vault 게이트 줄).

## 조사 결과
- 스킬 문서의 꼴: `skills/<name>/SKILL.md`, 머리말 `name`·`description`(끝에 "Not for …")·`argument-hint`(`skills/commit/SKILL.md:1-5`). 등록부 `skills/SKILLS.md`의 표(`:7-17`).
- Claude Code의 스킬 본문에서는 `$ARGUMENTS`와 `${CLAUDE_SESSION_ID}`가 치환된다(공식 문서 skills 절의 요약본에서 확인, 2026-11-24. 표 원문과 실제 치환은 미확인: 릴리스 뒤 첫 실행에서 확인한다). `disable-model-invocation: true`는 사용자가 `/이름`으로 부를 때만 실행되게 한다.
- 세션 노트의 경로는 `notes/sessions/YYYY/MM/YYYY-MM-DD-<세션 id 앞 8자>.md`(`C:\dev\vault\README.md`). 예약 작업이 10분마다 갱신하므로, 방금 시작한 세션은 노트가 아직 없을 수 있다.
- 틀: `notes/templates/problem.md`(증상·원인·패치·검증·관련 세션·다시 나왔을 때), `notes/templates/decision.md`(배경·결정·버린 안·근거·관련 세션·다시 볼 조건).
- `vault-sync check [파일 ...]`: 가림 처리가 바꿀 줄을 `경로:줄`로 내고 있으면 종료 코드 1(`C:\dev\vault\src\vault_sync\cli.py`). 걸린 파일이 있으면 예약 실행은 `notes/` 전체를 커밋하지 않는다.
- `knowledge/`의 노트는 git이 추적하는 `.md`라서, 쓰면 vault의 검증 증거가 무효가 된다(`hooks/worktree.py:313-325`): 스킬이 vault 게이트(`C:\dev\vault\CLAUDE.md`의 `gate:`)를 돌려야 Stop hook이 다시 요구하지 않는다. 게이트는 약 2분 30초.
- 다른 프로젝트의 세션에서 vault에 쓰는 것과 hook: 그 세션에 유효한 계획 승인이 있으면 vault의 파일은 범위 밖이라 Stop이 한 번 막힌다(`_scope_decision`, 첫 파일부터). 승인이 없으면 5개까지는 지적이 없다. 방금 적용한 규칙 `rules/plan-approval.md`는 "다른 저장소의 수정은 그 저장소의 계획이 필요하다"고 한다: 이 스킬이 쓰는 노트는 그 예외여야 하고, 그 예외를 규칙에 적어야 한다.

## 변경 범위
- C:/dev/_wt/claude-config/skills/distill/**
- C:/dev/_wt/claude-config/skills/SKILLS.md
- C:/dev/_wt/claude-config/rules/plan-approval.md
- C:/dev/_wt/claude-config/tests/test_docs_consistency.py
- C:/dev/_wt/claude-config/docs/plans/**
- C:/dev/_wt/claude-config/tests/_baseline/**
- C:/dev/vault/notes/knowledge/**

## 가정
- 이름은 `/distill`이다(2026-11-24의 설계에서 쓴 이름).
- 노트는 한국어로 쓴다(보관함의 다른 글과 같다). 스킬 문서는 다른 스킬처럼 영어다.
- 한 번에 노트는 3개까지다. 남길 것이 없으면 쓰지 않고 그렇게 보고한다(억지로 만들지 않는다).
- 인자가 있으면 이번 세션이 아니라 그 주제로 과거 세션 노트를 검색해 정리한다(예: `/distill commit hook이 막은 사례`). 과거 세션 정리는 읽을 양이 많으므로 `Explore` 에이전트에 검색을 맡길 수 있다.
- 이 세션에서의 시험 실행(4단계)은 스킬이 아직 설치되지 않은 상태에서 그 절차를 손으로 따라 하는 것이다. `${CLAUDE_SESSION_ID}`의 실제 치환은 릴리스 뒤 새 세션에서만 확인할 수 있어, 그 결과는 미확인으로 남을 수 있다. 치환되지 않았을 때의 길(가장 최근 기록 파일로 세션을 찾기)을 스킬에 적는다.
- 리뷰어의 결과 보고로 승인이 끊기면(이제 실제로 수정이 거부된다) 사용자에게 `task:distill-skill`로 시작하는 첫 줄을 요청한다. 그래서 편집과 계획서 기록을 모두 끝낸 뒤에 리뷰어를 돌린다.

## 선택한 접근
| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **A (선택)** | 사용자가 부르는 스킬(문서 하나). 정리는 그 세션의 Claude가 대화 내용을 보고 한다 | 대화의 맥락(왜 그렇게 했는지)을 그대로 쓴다. 코드가 없다. 가림 검사와 게이트는 이미 있는 것을 쓴다 | 부르지 않으면 쌓이지 않는다. 다른 프로젝트의 승인 중에 부르면 Stop이 한 번 막힌다 |
| B | 세션 종료 hook이 자동으로 요약 | 잊지 않는다 | hook에서 모델을 부를 수 없다(규칙 기반 요약은 지금의 세션 노트와 다를 바 없다). `~/.claude`의 hook 변경 |
| C | `vault_sync`에 요약 명령을 넣고 API로 모델 호출 | 예약 작업으로 돌릴 수 있다 | API 키·비용·네트워크. 대화 원문을 다시 외부로 보낸다(보관함의 "밖으로 내보내지 않는다" 원칙과 어긋남) |
A를 고른 이유: 가치가 생기는 지점(원인과 근거를 아는 순간)에 이미 있는 것만으로 된다.

## 스킬의 글 (`skills/distill/SKILL.md`)
```markdown
---
name: distill
description: Turn what this session learned into knowledge notes in the Obsidian vault (C:\dev\vault\notes\knowledge) - problems with their cause, patch and verification, and decisions with the options dropped - appending to an existing note when the same problem came back. Use when the user says distill, 정리, "남겨 둬", or asks to record what was learned before closing a task. With an argument, distills that topic from past session notes instead. Not for summarising a conversation (the session note already holds it), for project Gotchas (those go in the project's CLAUDE.md), or for anything that is not verified.
argument-hint: [topic to distill from past session notes; empty = this session]
disable-model-invocation: true
---

# Distill

Arguments: `$ARGUMENTS`. Session: `${CLAUDE_SESSION_ID}`.

The vault is `C:\dev\vault\notes`. `sessions/` and `env/` are generated: never edit them.
Hand-written notes live in `knowledge/`; that folder is the only place this skill writes.
Invoking this skill is the user's instruction to write these notes: it needs no plan of its
own, and it writes nothing else outside the session's project.

## 1. What is worth a note
- From this session (or, with an argument, from the session notes that match it: search
  `C:\dev\vault\notes\sessions` for the topic's error text, file names and task ids; hand a
  wide search to the `Explore` agent).
- A **problem**: something went wrong, its cause was found, and a patch or a workaround was
  verified. A **decision**: a real choice between options, with what was dropped and why.
- Only what is verified, with its evidence (`file:line`, a commit, a command and its
  result). A guess is written as a guess ("추정") or left out. No summary of the work, no
  praise, nothing the code or its README already says.
- At most three notes. Nothing reusable: write nothing and say so.

## 2. Find the note it belongs to
- Search `C:\dev\vault\notes\knowledge` for the error text, the file names and the key
  terms (Grep; file names there are short Korean sentences).
- The same problem is already there: append under `## 다시 나왔을 때` (problem) or
  `## 다시 볼 조건` (decision), with today's date, what was different this time, and the
  session link. Do not rewrite what the note already says.
- Otherwise a new note from `templates/problem.md` or `templates/decision.md`: copy the
  template, fill `{{title}}` and `{{date}}` (today, `YYYY-MM-DD`), set `area`, keep the
  section headings and delete the `<!-- ... -->` hints. File name: a short Korean sentence
  that says the problem (`커밋 리뷰 차단이 재시도로 우회됨.md`), no date in the name.

## 3. Write it
- Korean prose, short. 증상 with the error text as it was (a `text` code block); 원인 with
  the evidence; 패치 with the commit or the plan file; 검증 with the command and its result.
- Link the session note: its name is `YYYY-MM-DD-<first 8 characters of the session id>`,
  under `C:\dev\vault\notes\sessions\YYYY\MM\` (Glob `**/*-<first 8>.md`); write
  `[[YYYY-MM-DD-xxxxxxxx]]`. No such note yet (a session younger than 10 minutes): write the
  link by its expected name. `${CLAUDE_SESSION_ID}` printed above as a literal placeholder:
  take the id from the newest `.jsonl` under `~/.claude/projects/<this project's folder>/`.
- Never a secret value, not as an example either: write `[가림]` in its place. No HTML, no
  `![...]` embeds, no links other than `http(s)` and `[[...]]`.

## 4. Check, in this order
- `uv run --project C:/dev/vault vault-sync check <each file written>`: exit 0. A line is
  reported: mask or remove it and run again (until it passes, the vault commits nothing).
- The vault's gate, from `C:\dev\vault`, in the foreground, as the whole command line (its
  CLAUDE.md `## Verification` has the line; a note is tracked content there, so the gate's
  evidence is stale after writing one). Then go back to the session's own folder.
- The session holds an approved plan for its project: these notes are outside that plan,
  so the Stop reports them once as out of scope. Say in the report that they are `/distill`
  notes; do not revert them.

## 5. Report (Korean)
- Each note written or appended to, with its path and one line on what it says; what was
  left out and why (not verified, already recorded); the check and gate results.
```

## 규칙에 더할 문장 (`rules/plan-approval.md`의 둘째 항목 끝에)
```markdown
  One exception: a skill the user invokes may write what its own text names outside the
  session's project (`/distill`: notes under `C:\dev\vault\notes\knowledge`).
```

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-25-distill-skill.md`에 저장(`status: in-progress`). 최상위 계획 파일이 10개를 넘으므로 닫힌 계획 `2026-11-19-v1-release.md`를 `archive/`로 옮긴다 — 검증: 승인본과 status 줄만 다름.
- [x] 2. 테스트 먼저(`tests/test_docs_consistency.py`): `skills/distill/SKILL.md`가 있고, 머리말에 `disable-model-invocation: true`가 있고, 설명이 "Not for"를 담고, 본문이 말하는 틀 두 개와 `vault-sync check`, `## 다시 나왔을 때`·`## 다시 볼 조건` 제목이 글에 있고, `skills/SKILLS.md`에 `/distill` 행이 있고, 규칙에 예외 문장이 있다 — 검증: 파일이 없어 실패함을 확인.
- [x] 3. `skills/distill/SKILL.md`를 위의 글로 만들고, `skills/SKILLS.md`에 행을 추가하고, `rules/plan-approval.md`에 예외 문장을 넣는다 — 검증: 2단계 통과.
- [x] 4. 시험 실행: 스킬의 절차를 이 세션에 적용해 `C:\dev\vault\notes\knowledge`에 노트를 쓴다(이 세션에서 검증된 것 가운데 고른다. 후보: "`git commit -- <경로>`는 디스크를 다시 읽어 검사한 내용과 다른 것을 커밋한다", "검증 hook이 추적 파일 전체를 해시해 예약 작업이 증거를 무효로 만든다", "메시지의 '승인'을 승인으로 읽고 편집을 계속한 일"). `vault-sync check` → vault 게이트 → 다음 예약 실행이 노트를 커밋했는지 `git log`와 로그로 확인. 절차에서 막히거나 문서와 다른 점이 나오면 스킬의 글을 그에 맞게 고친다(글이 달라지면 멈추고 다시 승인받는다. 명령의 철자·경로 정정은 그대로 진행하고 기록한다) — 검증: 노트 경로, check 종료 코드 0, vault 게이트 통과, `notes: sync` 커밋에 그 노트가 있음.
- [x] 5. 게이트 `python -I tests/run_all.py`. 진행 기록을 쓰고 마지막 편집으로 `status: done` — 검증: `GATE PASSED`.
- [ ] 6. `code-reviewer`에게 미커밋 변경을 보임 → ship이면 커밋(스킬과 등록·규칙 문장·테스트 / 계획서와 보관 이동) → 릴리스(`/release-hooks`: `hooks/` 아래는 바뀌지 않으므로 지연 측정은 하지 않는다, `merge --ff-only`, 실제 hook 확인, `~/.claude`에서 게이트) → push → CI 확인. merge는 커밋과 같은 턴이면 Stop이 merge된 파일을 한 번 지적한다(방금 문서에 적은 대로): 그때는 보고에 적는다 — 검증: CI run id와 결과. 릴리스 뒤 `/distill`이 목록에 뜨는지, `${CLAUDE_SESSION_ID}`가 치환되는지는 새 세션에서 확인할 일로 보고에 남긴다.

## 리스크와 대응
- 노트에 비밀이 들어간다 → 스킬이 금지하고, `vault-sync check`가 모양 있는 것을 잡고, 걸리면 예약 실행이 커밋하지 않는다. 모양 없는 비밀은 못 잡는다(1부의 한계 그대로).
- 스킬이 다른 저장소에 쓰는 것이 방금 적용한 규칙과 어긋난다 → 규칙에 예외 한 문장을 넣는다(이 계획에서 승인받는다). 범위는 스킬의 글이 이름한 폴더로 한정한다.
- 노트가 쌓일수록 품질이 떨어진다(요약, 추정, 중복) → "검증된 것만, 3개까지, 없으면 쓰지 않는다, 같은 문제는 덧붙인다"를 스킬에 적었다. 쓸모는 실제 사용으로 판단한다(등록부의 사용 근거 문단).
- 시험 실행이 vault의 추적 파일을 바꾼다 → 변경 범위에 그 폴더를 넣었고, vault 게이트를 돌린다. vault에는 원격이 없어 push는 없다.
- vault 게이트를 돌리러 `cd`하면 세션 폴더가 다른 저장소로 바뀐다 → 게이트 뒤 바로 작업 트리로 돌아오고, 그 뒤의 편집 전에 `pwd`로 확인한다.
- `status: done` 뒤의 6단계는 계획서에 적지 못한다 → 최종 보고에 적는다.

## 영향 범위
새 스킬 하나(사용자가 부를 때만), 등록부 한 행, 규칙 한 문장(모든 세션), 일관성 테스트, vault의 `knowledge/`에 노트 1~3개. 새 세션부터 `/distill`을 쓸 수 있다.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-25 단계 1~3: 계획 저장, 닫힌 계획 `2026-11-19-v1-release.md`를 `archive/`로 옮김. `tests/test_docs_consistency.py`에 D14 세 검사를 먼저 써서 "파일 없음"으로 실패함을 확인한 뒤, `skills/distill/SKILL.md`를 계획의 글 그대로 만들고 `skills/SKILLS.md`에 행 추가, `rules/plan-approval.md`의 둘째 항목에 예외 문장 추가. 계획대로.
- 2026-11-25 단계 4(시험 실행): 스킬의 절차를 이 세션에 적용. `knowledge/`에 기존 노트가 없어 `templates/problem.md` 꼴로 새 노트 셋을 씀: "git commit에 경로를 주면 검사한 내용이 아니라 디스크의 내용이 커밋됨", "예약 작업이 추적 파일을 고칠 때마다 검증 증거가 무효가 됨", "메시지의 승인이라는 말을 계획 승인으로 읽고 편집을 계속함". 세션 링크는 `[[2026-11-23-00ce1b57]]`(Glob으로 찾은 실제 노트). `uv run --project C:/dev/vault vault-sync check <파일 셋>` 종료 코드 0(다른 폴더에서 실행해도 동작함을 확인). vault 게이트 412 passed. 예약 작업을 실행해 `notes: sync` 커밋 `742de7d`에 세 노트가 들어간 것을 확인(새 커밋 방식이 실제 보관함에서 만든 첫 커밋, `.git`에 임시 색인 파일 남지 않음). 스킬의 글과 달랐던 점: 처음에 `found`에 문제가 생긴 날(2026-11-24)을 적었다. 스킬은 `{{date}}`를 오늘로 채우라고 하므로 2026-11-25로 고치고 check와 vault 게이트를 다시 통과시켰다(실제 발생일은 본문 "증상"에 있다). 스킬의 글은 바꾸지 않았다. 시험에서 드러난 빈 곳(글을 바꾸지 않고 남김): 머리말의 `status` 값을 무엇으로 둘지 스킬이 말하지 않는다(시험에서는 `resolved`로 적음). vault 게이트를 돌리려면 `cd`로 폴더를 옮겨야 해서 세션 폴더가 잠시 다른 저장소가 된다(돌아온 뒤 `pwd`로 확인함).
- 2026-11-25 단계 5: 게이트 `python -I tests/run_all.py` → 45개 모듈, 2640개 검사, `GATE PASSED`. 6단계(리뷰, 커밋, 릴리스, push, CI)의 결과는 `done` 뒤의 일이라 여기에 적지 못한다: 최종 보고에 적는다.
