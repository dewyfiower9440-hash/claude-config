<!-- 초안, 미적용 (2026-11-25). 사용자 확인 전에는 규칙이 아니다.
     여기(docs/plans/archive/)에 둔 이유: Claude Code는 rules/ 아래의 .md를 하위 폴더까지 모두
     읽고, paths: 머리말이 없으면 모든 세션에 로드한다(공식 문서 memory 절). rules/drafts/에
     두면 ~/.claude에 반영되는 순간 적용되므로 릴리스 전에 옮겼다. 이 폴더는 hook이 읽지 않는다.
     적용하려면: 문장을 확정하고, 이 파일을 rules/ 바로 아래로 옮길지 CLAUDE.md 2절에 한두
     문장으로 넣을지 정한다. rules/ 바로 아래에 두려면 tests/test_docs_consistency.py가
     rules/*.md를 모두 언어 규칙으로 읽는 부분(Quality gate 검사)을 함께 고쳐야 한다.
     paths: 머리말이 없는 규칙 파일은 모든 세션에 읽힌다. -->

# Plan approval follows the folder (draft)

An approved plan is bound to one repository: the git root of the folder the session was in
when the plan was approved. The hook looks for the saved plan in that root's `docs/plans`
and nowhere else.

- **Start the session in the project root.** An approval given from another folder is
  bound to that folder's repository; saving the plan in the intended project is then refused
  as "not the approved plan". Observed 2026-11-23: the shell had moved to
  `C:\dev\tools\scaffold` through a `cd`, and the plan for `C:\dev\vault` was approved there.
- **A `cd` into another repository does not take the approval along.** The hook says so at
  the next prompt ("작업 폴더가 이 세션에서 처음 보는 저장소로 바뀌었습니다"). Edits there need
  that repository's own approved plan. Before presenting a plan, check where the session is
  (`pwd`, `git rev-parse --show-toplevel`) and go back to the project root first.
- **Two repositories, two plans.** A task that changes two repositories is planned and
  approved once in each, one after the other, each from its own root. A plan's
  `## 변경 범위` may name absolute paths, but another repository's `docs/plans/` is not covered.
- **Prefer absolute paths over `cd` for reading.** `git -C <path>`, absolute paths with the
  file tools: the session stays where its approval is. In Git Bash a `cd` outside the working
  directories is undone after the command anyway; one inside them persists.
- **Closing a plan (`status: done`) ends its approval at once.** Make it the last edit of
  the task, after the progress record and Gotchas, and commit afterwards. This is the end of
  a finished task, not a way around a lost approval: once an approval is lost, closing,
  moving or deleting the plan file changes nothing, and only the user continues the work
  (a first line that starts with `task:<id>`, a new approval, or `revoke task:<id>`).
- **A plan parked as `blocked` waits too.** It grants no approval, and the task line does not
  bring one back while the plan stays blocked: the user sets it to `in-progress` again and
  then starts a prompt with `task:<id>`, or lays the task down with `revoke task:<id>`.
- **A message is not an approval.** "승인", "approved", "go on" in the user's message do
  not continue a lost approval, and neither does a task id mentioned in the middle of a
  line. When the hook says the approval was lost, stop editing and ask for the line.
