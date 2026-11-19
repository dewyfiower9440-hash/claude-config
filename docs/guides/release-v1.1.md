# 개발환경 v1.1 릴리스 노트 (2026-11-20)

v1.1은 세션이 서브에이전트·스킬·MCP를 **실제로 쓰도록** hook이 그 행동이 일어나는 순간에 알리거나 한 번 막게 한 패치다. 규칙 문서만으로는 호출이 일어나지 않는다는 것이 확인됐기 때문이다. 2026-11-19 프로젝트 세션(5시간, 도구 호출 596회, 파일 87개, 커밋 3건)에서 서브에이전트·스킬 호출은 0회, MCP는 2회였다.

- 태그: `v1.1`은 커밋 `bd2365f`를 가리킨다(훅 코드 기준). 운영 main은 그 뒤 문서·CI 설정 커밋을 더 담고 있으며 훅 코드는 같다.
- 계획과 진행 기록: `docs/plans/2026-11-19-devenv-v1-1.md`(status: done)
- 이전 버전: `v1.0`(`6a423d2`), 기록은 `docs/plans/2026-11-19-v1-release.md`

## 1. 무엇이 바뀌었나

| 때 | 바뀐 동작 | 기본 | 조절 |
|---|---|---|---|
| `git commit` 직전 | 리뷰 기준(코드 파일 3개 이상, 150줄 이상, 위험 경로, 테스트와 구현 동시 변경)에 해당하는 변경을, 끝난 `code-reviewer`가 본 적 없는 내용으로 커밋하면 **한 번 거부**한다. 같은 내용으로 다시 실행하면 통과한다. `/commit` 스킬 안에서 하는 커밋은 거부 대신 알림이다 | 켜짐 | `CLAUDE_COMMIT_REVIEW=block`·`notify`·`off` |
| 턴 종료(Stop) | 리뷰 기준에 해당하는 변경의 리뷰를 한 번 요구한다(기존 리뷰 게이트를 기본으로 켬) | 켜짐 | `CLAUDE_REVIEW_GATE=1`, 빼면 기록만 |
| 큰 셸 출력 직후 | 테스트 명령이면 `test-runner`, 그 밖이면 `Explore`·`general-purpose` 위임을 권한다. 파일로 저장된 출력(`<persisted-output>`)도 포함한다. 턴당 한 번 | 켜짐 | `CLAUDE_OUTPUT_ADVICE_CHARS`(기본 8000자) |
| 의존성 추가 직후 | `uv add`·`pip install`·`pnpm add`·`npm install <패키지>`·`cargo add`를 했는데 세션에 context7 조회 기록이 없으면 `researcher`(context7) 확인을 권한다. 턴당 한 번 | 켜짐 | 없음(알림만) |
| 계획 파일 작성 직후 | `docs/plans/*.md`를 `/plan`·`/implement` 스킬 없이 쓰면 `/plan` 사용, Write 도구로 저장, ExitPlanMode 승인을 안내한다. ExitPlanMode 직후 저장과 승인된 계획 작업에는 나오지 않는다 | 켜짐 | 없음(알림만) |
| 승인 취소 | 승인 뒤 계획 본문이 바뀌어 취소되면 `plan_changed(변경 범위)`처럼 **바뀐 부분**을 이름으로 알리고, 재승인 또는 `## 진행 기록`에 적는 방법을 안내한다 | 켜짐 | 없음 |
| 5파일 한도 거부 | "승인받은 계획은 Write 도구로 `docs/plans/`에 저장(셸 리다이렉트는 분류기가 막음)" 한 문장이 붙는다 | 켜짐 | `CLAUDE_MAX_FILES_WITHOUT_PLAN` |
| 모든 Skill·MCP 호출 | post hook이 호출을 세션 상태에 기록한다(스킬 이름과 턴, MCP 서버별 횟수; 인자는 남기지 않음). 위 알림들이 "안 썼다"를 판정하는 근거다 | 켜짐 | 없음 |

문서도 맞췄다: `CLAUDE.md` §2b·§8, `skills/commit/SKILL.md`(Claude가 스스로 커밋할 때도 사용), `skills/plan/SKILL.md`(Write 도구로 저장, `plan_changed` 읽는 법), `agents/code-reviewer.md`, `skills/SKILLS.md`, `docs/guides/working-in-a-new-session.md`.

## 2. 세션에서 보게 되는 것

- 커밋이 한 번 막히면: 그 변경을 `code-reviewer`로 리뷰하고 CONFIRMED 지적을 고친 뒤 커밋한다. 리뷰 없이 그대로 다시 커밋했다면 보고에 그렇게 적는다. 리뷰는 시작할 때 본 내용에만 유효하다: 리뷰 뒤 코드를 다시 고치면 다음 커밋에서 다시 요구된다.
- 알림(Explore·context7·/plan)은 차단이 아니다. 턴당 종류별로 한 번만 나온다.
- 승인이 `plan_changed(…)`로 취소되면: 범위·단계를 바꾼 것이면 ExitPlanMode로 재승인, 표기만 고치려던 것이면 본문 대신 `## 진행 기록`에 적는다.

## 3. 검증

| 항목 | 결과 |
|---|---|
| 로컬 게이트 `python -I tests/run_all.py` | GATE PASSED, 43 모듈·2502 검사(작업트리와 운영본 모두) |
| CI | run 44559313607 성공(커밋 `f9a72c7`, windows-latest, 43 모듈·2503 검사; 로컬보다 1개 많은 것은 CI 출력에 그대로 적힌 수): **CI Verified** |
| 훅 지연(p50) | 느려지지 않음. 다만 측정 환경 차이가 크다: v1.0 기준선 `2026-11-19-guard-b15-final.json`은 Python 3.14.2, `2026-11-19-v1-1.json`은 3.13.5로 쟀고, 바뀌지 않은 guard도 191→131ms로 나왔다. 같은 3.13.5로 잰 `2026-11-18-final-step30.json`과는 guard 121→131ms, pre 328→320ms, post 504→495ms(릴리스 기준인 +20% 안). 차이는 측정 잡음 범위로 보며, 빨라졌다는 뜻이 아니다 |
| 승인 공격·정상 corpus | 종전과 같음(A7·A8은 enforce에서만 통과하는 사례) |
| 실측 | 임시 프로젝트에서 리뷰 없는 3파일 커밋이 1회 거부되고, 다시 실행하면 훅이 통과시킴 |
| 리뷰 | code-reviewer(1차 fix first → 수정 → 2차 ship), security-auditor(low만) |

새 테스트: `tests/test_usage_state.py`, `tests/test_commit_review.py`; 확장: `test_output_size`, `test_approvals_store`, `test_approvals_v2`, `test_shellwrites`.

## 4. 함께 바뀐 운영 사항

- **CI가 다시 돈다.** private 저장소의 Actions가 계정 결제·지출 한도로 2026-11-18부터 시작되지 않았다. 저장소 `claude-config`를 **public**으로 전환했다(2026-11-19, 사용자 결정). public 저장소의 표준 GitHub 호스팅 runner는 무료다. 전환 전에 그 시점의 전체 이력(커밋 253개)을 검사했다: 비밀 모양 문자열 9건은 모두 테스트용 가짜 값이고, 작성자 이메일은 전부 GitHub noreply다. 이제 코드·이력·Actions 로그가 공개된다.
- **CI 환경**: workflow가 게이트 전에 `~/.claude/hooks` 폴더를 만든다. 가드는 `~/.c?aude` 같은 패턴을 디스크에서 펼쳐 판단하는데, runner에는 그 폴더가 없어 가드 검사 4개가 CI에서만 실패했다(v1.0 때 추가된 검사로, 결제 차단 때문에 CI에서 한 번도 돌지 않았음).

## 5. 알려진 한계 (다음 작업 후보)

- 커밋 검사가 보지 못하는 커밋: `--git-dir=`·`--work-tree=`로 다른 저장소에 하는 커밋, `sh -c "git commit"`, git alias(`git ci`), 서브에이전트가 한 커밋. `git commit -m '--dry-run'`은 dry-run으로 잘못 읽힌다.
- `Skill(commit)`을 같은 턴에 부르기만 해도 거부가 알림으로 바뀐다(`/commit` 스킬 체크리스트에는 리뷰 단계가 없다).
- PowerShell에서 `git -C /c/...`를 Git Bash식으로 해석한다.
- 가드의 패턴 판정은 `~/.claude`가 디스크에 있어야 막는다(실제 PC에는 항상 있다).
- Skill 도구가 PostToolUse를 발생시키는지는 실측하지 않았다(미확인). 공식 문서는 PreToolUse만 명시한다.
- MCP 호출마다 post hook이 돈다. MCP 호출의 지연은 따로 재지 않았다.
- `CLAUDE.md`가 100줄 한도에 닿아 Gotchas를 더할 수 없다(백로그 B-1).

## 6. 되돌리기

- 한 기능만: 환경변수로 끈다(`settings.json`의 `env`: `CLAUDE_COMMIT_REVIEW=off`, `CLAUDE_REVIEW_GATE` 삭제).
- 전부: 자기 터미널에서 `git -C ~/.claude checkout v1.0 -- hooks settings.json`(CLAUDE.md Release 절차). 그 뒤 작업트리에서 고쳐 다시 릴리스한다.
