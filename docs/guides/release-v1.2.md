# 개발환경 v1.2 릴리스 노트 (2026-11-23)

v1.2는 v1.1 hook이 실제 세션에서 **우회되거나 빗나간 곳**을 고친 패치다. 근거는 세션 `2ea67f50`(quant, 11-19~11-22, 도구 호출 1517회, 서브에이전트 transcript 26개)과 `4aec7c96`(v1.1 구현 세션)의 집계, 그리고 10분 주기 감시 보고서다. 호출이 실패한 경우는 0건이었다. 원인은 "알면서 부르지 않음"과 "hook이 우회를 허용함"이었다.

- 계획과 진행 기록: `docs/plans/2026-11-20-devenv-v1-2.md`
- 이전 버전: `v1.1`(`c72dc04`), 기록은 `docs/guides/release-v1.1.md`

## 1. 무엇이 바뀌었나

| 때 | 바뀐 동작 | 근거 | 조절 |
|---|---|---|---|
| `git commit` 직전 | 리뷰 기준에 해당하는 미리뷰 커밋을 **매번** 막는다(전에는 한 번만 막고 재실행은 통과). `/commit` 스킬 안에서도 막는다(사용자 결정 2026-11-23: 스킬은 리뷰어를 부르지 않음). 통과하려면 리뷰를 받거나, 커밋 메시지에 `Review-Skipped: <이유>` 줄을 넣는다(`-m`, `-F <파일>`, `-F -`의 heredoc·`echo`·`printf`, `--trailer`). 그 줄은 git 이력에 남고, 커밋 뒤 "리뷰 생략 기록" 알림이 나온다 | 차단 6회 중 5회가 6~10초 뒤 재실행으로 통과 | `CLAUDE_COMMIT_REVIEW=block`·`notify`·`off` |
| 셸 명령 직전 | 백슬래시가 든 내용으로 파일을 쓰는 인라인 패치(`python -c`·python heredoc·`cat`/`tee` heredoc)를 **명령마다 한 번** 막고, Edit/Write나 Write로 만든 스크립트 파일 + repr 확인을 안내한다. 같은 명령을 다시 실행하면 통과한다(의도한 백슬래시) | `\r`이 CR 바이트가 되어 파일이 깨짐(2026-11-20) | `CLAUDE_INLINE_PATCH=block`·`notify`·`off` |
| `git -C <dir> push` 뒤 | push를 `<dir>`의 저장소·브랜치로 기록한다(전에는 세션 폴더의 브랜치). Stop hook이 그 run을 찾는다 | 작업트리에서 `git -C ~/.claude push` 하면 "CI 미확인" 오경보 반복 | 없음 |
| 큰 테스트 출력 뒤 | `test-runner` 알림은 **세션당 3회**까지(초기값). 문구는 출력 줄이기(`-q`, 파일로 보낸 뒤 `tail`)가 먼저다 | 알림 15회, 수용 0회 | 글자 수 기준만 `CLAUDE_OUTPUT_ADVICE_CHARS`. 상한 3회는 코드 상수(`MAX_TEST_ADVICE_PER_SESSION`)이고 환경변수가 없다 |
| 의존성 추가·보안 파일 편집 뒤 | 세션에 `security-auditor` 기록이 없으면 **세션당 한 번** 알린다. 파일은 저장소 안 경로의 단어(auth·oauth·crypt·secret 접두어, sign·token·key·password 계열)로 판단하고, 테스트·계획·무시 파일은 제외한다 | `uv add cryptography`로 주문 서명을 넣었는데 감사 0회 | 없음(알림만) |
| `/commit` 밖의 직접 커밋 뒤 | **세션당 한 번** `/commit` 사용을 안내한다 | `/commit` 1회, 직접 커밋 59회 | `CLAUDE_COMMIT_REVIEW=off`면 안 나옴 |
| 리뷰어 정의 | `agents/code-reviewer.md`: Write/Edit 도구가 없으니 부르지 않는다, 인라인 확인 우선, 파일은 변수 없는 리터럴 temp 경로에만(본문에 백슬래시 없이), 검사 하나는 5분 이내(초기값), 파이프라인·백테스트·전체 스위트는 돌리지 않고 PLAUSIBLE | 없는 Write 호출 16회(13회는 재호출), 변수 경로 guard 차단 8회, 72분 백테스트 | 없음 |

문서도 맞췄다: `CLAUDE.md` §2b·§8·§9, `skills/commit/SKILL.md`, `docs/guides/working-in-a-new-session.md`.

## 2. 세션에서 보게 되는 것

- 커밋이 막히면: `code-reviewer`로 리뷰한다. 리뷰 지적을 고친 뒤라면 고친 부분만 다시 리뷰받는다. 재시도로는 통과하지 않는다. 리뷰를 일부러 생략해야 하면 메시지에 `Review-Skipped: <이유>`를 넣고 보고에 적는다.
- 인라인 패치가 막히면: Edit/Write 도구로 바꾼다. 백슬래시가 의도한 그대로라는 것을 확인했으면 같은 명령을 다시 실행한다.
- 세션당 한 번 나오는 알림(`/commit`, `security-auditor`)은 차단이 아니다.

## 3. 검증

| 항목 | 결과 |
|---|---|
| 로컬 게이트 `python -I tests/run_all.py` | GATE PASSED, 45 모듈·2571 검사(작업트리, 커밋 `c320b31`) |
| CI | run 45346735243 성공(커밋 `c320b31`, push, job `hooks`): **CI Verified**. 이 문서를 고친 마무리 커밋의 run id는 태그 `v1.2`의 메시지에 적는다 |
| 훅 지연(p50) | `tests/_baseline/2026-11-23-v1-2.json`. 저장된 v1.1 기준(11-19) 대비 pre −13.2%, prompt −6.1%, guard +8.6%, post +14.4%, stop +27.9%. 바뀌지 않은 guard·format_lint도 +9~16%여서 같은 시각에 main(v1.1)과 v1.2 사본을 번갈아 두 번씩 쟀다: stop은 316.8/297.0 ms 대 334.4/267.6 ms, post는 580.2/531.1 ms 대 819.8/479.3 ms(v1.2 첫 회는 p95 6046 ms짜리 튄 실행). 코드로 인한 일관된 증가는 보이지 않았다. 두 번씩만 잰 것이라 작은 차이는 가려내지 못한다 |
| 승인 공격·정상 corpus | v1.1과 같음(13건, A7·A8은 enforce에서만 통과하는 사례) |
| 실측: 운영 22시간(11-23 18:02 merge ~ 11-24 16:00) | 차단된 커밋 4건이 모두 리뷰(3건: vault `d5bc394`, `c93baca` 외 2개, quant `2aa7f43`)나 `Review-Skipped` 줄(1건: vault `f0d55ba`)을 거쳐 통과했다. 재시도로 통과한 커밋은 0건. 인라인 패치 차단 1건(quant, `cat` heredoc), 직접 커밋 알림 1건(vault) |
| 실측에서 본 것 | 재리뷰는 짧을 수 있다(문서 3개 diff를 리뷰어가 13초 만에 확인): hook은 "끝난 리뷰어가 이 내용을 봤다"만 본다. guard 타임아웃 1건(11-24 08:51, 원인 미확인; guard는 v1.2에서 바뀌지 않음). 테스트 알림 상한 3회는 운영에서 확인하지 못했다 |

새 테스트: `tests/test_inline_patch.py`, `tests/test_security_advice.py`. 확장: `test_ci_status`(git -C push), `test_commit_review`(재시도 차단·트레일러·메시지 파싱·직접 커밋 알림), `test_output_size`(세션 상한), `test_docs_consistency`(D11 리뷰어 정의와 guard).

## 4. 알려진 한계

- 커밋 메시지: PowerShell backtick 이스케이프(`` `n``)는 풀지 않는다(그렇게 쓴 트레일러는 인식되지 않아 거부된다, 안전한 쪽). `-F` 파일 경로의 변수(`$TEMP`, `$env:TEMP`)도 풀지 않는다: 읽지 못한 메시지는 트레일러가 없는 것으로 보고 거부하며, 거부 문구는 그 이유를 따로 말하지 않는다. `-F`에는 리터럴 전체 경로를 쓴다(`skills/commit/SKILL.md`). 커밋 검사가 보지 못하는 커밋은 v1.1과 같다(`--git-dir`, `sh -c`, alias, 서브에이전트).
- 인라인 패치: 주 세션의 명령만 본다(서브에이전트의 명령은 검사하지 않는다). 보지 못하는 경우는 `hooks/inline_patch.py`의 docstring과 코드 주석에 있다(`| tee f`·`| Out-File`로 이어지는 출력, `cat <<EOF | python -`, `bash -c "..."`, `sed -i`·`perl -pi`, `2 > f`처럼 띄어 쓴 2, 괄호가 두 단계 넘게 중첩된 `open(...)`). 같은 명령 재실행이 탈출구이므로 재실행 비율은 감시 보고서로 잰다.
- 보안 알림: `author.py` 같은 단어는 오탐이다. 상대 경로의 `..`, 저장소 위 폴더 이름이 `tests`인 경우, `track_repo`가 저장소를 못 찾는 경우(예산 소진)에는 판단이 달라질 수 있다(알림만이라 허용).
- 측정으로 다시 정할 값: `MAX_TEST_ADVICE_PER_SESSION=3`, 리뷰어 검사 5분, `Review-Skipped` 사용 수(많으면 "리뷰 뒤 작은 변경 면제" 접근을 따로 결정).

## 5. 되돌리기

- 한 기능만: `settings.json`의 `env`에 `CLAUDE_COMMIT_REVIEW=notify`(또는 `off`), `CLAUDE_INLINE_PATCH=off`.
- 전부: 자기 터미널에서 `git -C ~/.claude checkout v1.1 -- hooks settings.json`(CLAUDE.md Release 절차). 그 뒤 작업트리에서 고쳐 다시 릴리스한다. 이 명령은 문서(`CLAUDE.md`, `skills/commit/SKILL.md`, `agents/code-reviewer.md`)를 되돌리지 않는다: 그동안 문서는 "매번 막힘"이라 해도 hook은 v1.1처럼 한 번만 막는다. 문서까지 맞추려면 그 세 경로도 같은 명령에 넣는다.
