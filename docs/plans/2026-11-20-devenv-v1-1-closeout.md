---
status: in-progress
task: devenv-v1-1-closeout
---

# 개발환경 v1.1 마무리: 남은 미확인 3건을 실측으로 닫고 v1.1에 반영

- 저장 위치: `C:\dev\_wt\claude-config\docs\plans\2026-11-20-devenv-v1-1-closeout.md` (브랜치 `devenv-final`)
- 근거: 사용자 지시(2026-11-20, 그대로 인용) "미완료·리스크: Skill 도구의 PostToolUse 발생 여부 미실측(/plan 알림·Skill(commit) 강등 로직에 영향) / e2e 실측에서 재시도 커밋은 훅 통과만 확인, 실제 커밋 생성은 미확인(git add가 거부된 명령에 포함) / 태그 커밋의 자체 CI green 없음(후속 커밋으로 대체 확인) — 상세히 확인후 마저 해결후 v1.1에 적용"
- push: 위 지시("v1.1에 적용")에 따라 이 작업의 push(main, 태그)는 묻지 않고 진행한다.

## Context
v1.1(`docs/plans/2026-11-19-devenv-v1-1.md`, done)은 CI Verified(run 44564778826, bcfb34f)까지 갔지만 세 가지가 실측되지 않았다.
1. Skill 도구 호출이 PostToolUse를 발생시키는지. 공식 hooks 문서는 PreToolUse만 명시한다. 발생하지 않으면 `state["skills"]`가 비어 `/plan` 알림이 스킬을 쓴 턴에도 나오고, `/commit` 안의 커밋이 알림이 아니라 거부가 된다(`hooks/discipline.py` `note_skill_use`, `skill_used_this_turn`, `commit_review_denial`).
2. 거부 뒤 재시도한 커밋이 실제로 만들어지는지. 지난 실측은 `git add -A && git commit`이 통째로 거부되어 재시도 때 스테이징이 없었다.
3. 태그 `v1.1`(bd2365f)의 자체 CI. 그 커밋의 workflow에는 `~/.claude/hooks` 폴더 단계가 없어, 같은 훅·테스트인 6b156ab의 run 44551185583(attempt 2)이 가드 검사 4개로 실패했다. 즉 bd2365f는 재실행해도 green이 될 수 없다.

## 변경 범위
- `C:/dev/_wt/claude-config/docs/**`
- `C:/dev/_e2e-v11b/**` (실측용 임시 저장소, 확인 뒤 삭제)
- `C:/Users/user/.claude/**` (운영본: `merge --ff-only`·`git tag`·`git push`만. Edit·Write로 고치지 않는다)

## 결정 (이 계획의 승인으로 정하는 것)
- 태그 `v1.1`을 CI green인 최종 커밋으로 **옮긴다**. 원격 태그를 덮어쓰므로(`git push --force origin refs/tags/v1.1`) 전역 규칙의 "never force-push"에 대한 이 작업 한정 예외다. main 브랜치는 force하지 않는다. 옛 위치는 태그 `v1.1-pre`(bd2365f)로 남겨 기록을 보존한다.
- 대안(채택 안 함): `v1.1.1` 새 태그(덮어쓰기 없음). 지시가 "v1.1에 적용"이라 채택하지 않았다.

## 단계
- [x] 1. Skill의 PostToolUse 실측 — 임시 저장소 `C:\dev\_e2e-v11b`(git init, CLAUDE.md에 `gate: \`python -I -c "print(1)"\``, `ci: none`)에 `.py` 3개를 쓰고 `git add`를 따로 실행한 뒤, Skill 도구로 `commit` 스킬을 호출하고 같은 턴에 `git commit`을 실행한다. 기대: 거부 없음, PostToolUse 알림에 code-reviewer 문구가 있고 `/commit` 안내는 없음(= Skill 기록이 남았다는 관측 증거). 반대로 거부되고 `/commit` 안내가 나오면 Skill PostToolUse가 오지 않는 것이다 → 아래 "멈춤 조건".
- [x] 2. 재시도 커밋 실측 — 같은 저장소에서 내용을 새로 바꿔 `git add`(따로) → Skill 없이 `git commit` → 1회 거부 확인 → 같은 명령 재실행 → `git log`에 그 커밋이 생겼는지 확인. 그 뒤 선언한 게이트를 실행하고 저장소를 삭제한다.
- [x] 3. 문서 반영 — `docs/guides/release-v1.1.md` §3·§5(실측 결과, 태그 위치), `docs/plans/2026-11-19-devenv-v1-1.md` 진행 기록에 한 줄, 이 계획 파일 진행 기록. 검증: `python -I tests/run_all.py` GATE PASSED.
- [ ] 4. 릴리스 — 커밋 → 게이트 → `git -C ~/.claude merge --ff-only devenv-final` → push main → CI run을 끝까지 확인(green이어야 함).
- [ ] 5. 태그 — `git -C ~/.claude tag -a v1.1-pre bd2365f` push, 그 뒤 `git -C ~/.claude tag -f -a v1.1 <4의 커밋>` → `git push --force origin refs/tags/v1.1`. 검증: `git ls-remote --tags origin`에서 `v1.1^{}`이 4의 커밋, `v1.1-pre^{}`이 bd2365f; 그 커밋의 CI run이 success.

## 멈춤 조건
- 1에서 Skill PostToolUse가 오지 않으면: 문서의 "미확인"을 "실측: 오지 않음"으로 바꾸고, 강등·/plan 알림 로직의 수정(예: PreToolUse에서 Skill을 기록)은 별도 계획으로 남긴 채 보고한다(설계 결정이 필요).
- 4의 CI가 실패하면 고치지 않고 원인을 보고한다.

## 리스크
- 임시 저장소 삭제 뒤 "추적하던 저장소가 사라짐" 알림: 선언한 게이트를 삭제 전에 통과시켜 미검증 변경이 없게 한다. 그래도 나오면 사용자에게 `ack-missing C:\dev\_e2e-v11b`를 안내한다.
- 태그 덮어쓰기: 이미 v1.1을 받아 간 곳이 있으면 옛 커밋을 가리킨다(이 저장소는 1인 사용, 영향 작음). `v1.1-pre`로 옛 위치를 보존한다.

## 검증 (전체)
- 1·2의 훅 출력과 `git log`를 보고에 그대로 인용.
- 로컬 게이트 GATE PASSED, 4의 CI run id와 success, 5의 `ls-remote` 결과.

## 진행 기록
- 2026-11-20 단계 1: Skill 도구로 commit 스킬 호출 → 같은 턴 `git commit`(3파일, 따로 `git add`) → 거부 없음, PostToolUse 알림 "방금 커밋한 C:\dev\_e2e-v11b의 변경은 리뷰 기준(코드 파일 3개)에 해당 … (알림이며 차단이 아닙니다.)"에 `/commit` 안내 없음 → Skill PostToolUse 발생·기록 확인. 커밋 0f96a8f — 계획대로.
- 2026-11-20 단계 2: 계획과 다른 점: 이 턴에 이미 Skill(commit) 기록이 있어 거부가 생기지 않으므로, 운영 hook 스크립트(`~/.claude/hooks/discipline.py`)를 새 세션 id로 직접 구동(상태는 scratchpad의 격리 폴더). 결과: 1차 pre deny("커밋 전 독립 리뷰가 없습니다 … missing"), 2차 pre 결정 없음(허용), 실제 git 커밋 0f96a8f→c767393 생성. 선언 게이트 `python -I -c "print(1)"` 통과 뒤 `C:\dev\_e2e-v11b` 삭제.
- 2026-11-20 단계 3: release-v1.1.md(태그 위치와 bd2365f가 green이 될 수 없는 근거, 실측 2행, 알려진 한계에서 Skill 미확인 삭제·`/이름` 직접 입력은 hook을 거치지 않음 추가), v1.1 계획 진행 기록 한 줄 — 게이트 GATE PASSED(43 모듈, 2502 검사). 계획대로.
