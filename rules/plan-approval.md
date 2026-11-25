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
  One exception, and only this one: `/distill`, run by the user, writes its notes under
  `C:\dev\vault\notes\knowledge`. The hook makes no exception and may report the write.
- **Two repositories, two plans.** A task that changes two repositories is planned and
  approved once in each, one after the other, each from its own root.
- **A message is not an approval.** "승인", "approved", "go on" in the user's message do
  not continue an approval the session no longer holds, and neither does a task id
  mentioned in the middle of a line. The hook then refuses every edit in that repository.
  Stop editing and ask the user for one of: a prompt whose first line starts with
  `task:<id>`, the plan presented again and approved (ExitPlanMode), or a first line
  `revoke task:<id>` to lay the task down. Do not look for another way to write the files.
- **What is done to the plan file does not bring an approval back.** Closing, moving or
  deleting it after the approval is gone changes nothing. A plan parked as `blocked` has
  lost its approval for good: setting it back to `in-progress` and a `task:<id>` line do
  not continue it. It is presented again and approved (ExitPlanMode), or laid down with
  `revoke task:<id>`.
- **Closing a plan (`status: done`) ends its approval at once.** Make it the last edit of
  the task, after the progress record and Gotchas, and commit afterwards.
- **A subagent's report can end the turn's approval.** Finish the edits that need the
  approval before starting reviewers, or expect to ask for the `task:<id>` line afterwards.
