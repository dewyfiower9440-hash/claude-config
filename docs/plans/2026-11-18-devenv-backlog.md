---
status: draft
task: devenv-backlog
---

# devenv 백로그 (2026-11-18 기준)

이 파일은 계획이 아니라 목록이다(`status: draft`: 승인도 범위도 주지 않는다). `docs/plans/2026-11-18-devenv-final.md`가 미룬 것, 그 계획의 비목표, 닫힌 계획(마스터·P4·P5)에서 넘어온 것을 모았다. 항목을 시작할 때는 그 항목만으로 `/plan`을 새로 만든다. 닫힌 계획의 원문은 `docs/plans/archive/`에 있다.

## 목표
남은 일을 한 곳에서 보고 우선순위를 사용자가 정하게 한다. 비목표: 여기서 구현하지 않는다.

## A. 사용자 조치가 먼저 필요한 것
- **GitHub Actions가 시작되지 않는다(2026-11-18)**: run 44274768513, 44281868140이 "recent account payments have failed or your spending limit needs to be increased"로 2초 만에 실패했다. Billing & plans에서 풀어야 한다. 풀린 뒤: `gh run rerun <마지막 run id>`로 main의 마지막 커밋을 끝까지 확인(구간 4 이후는 모두 Locally Verified까지만이다). `C:\dev\tools`, memo-board도 같은 계정이다(미확인).
- enforce 전환 시점(`CLAUDE_AUTHZ_MODE=enforce`; 원래 2026-11-22 이후로 잡혀 있었다, P4-11·P5-03). 전환 때 `CLAUDE.md` §2의 warn 문장 2개를 고친다.
- 평가 환경(P3-01: 평가자 계정 또는 VM, 과제 저장소, 홀드아웃 30%)과 예비 측정 35회(P5-06). `docs/plans/2026-11-15-devenv-p3.md`가 `blocked`로 남아 있다.
- 새 세션에서 `/doctor`, `/context`, `/skill-doctor` 결과 확인(컨텍스트 영역의 80점 기준 하나가 `/doctor` 경고 0이다).
- auto mode에서 `ask` 확인 창이 실제로 뜨는지 확인(P5-01).
- claude.ai 설정에서 개발에 쓰지 않는 커넥터와 동기화 skill 끄기(P5-12 S4).
- 작업 강도(`/effort`) 기본값, quant-research 수집기 복구.
- 실제 프로젝트에서 `/fewer-permission-prompts`를 돌려 허용 후보를 뽑는다(devenv-final 단계 32는 계획이 이름으로 든 5개 명령만 넣었다).
- `CLAUDE.md` 축약의 결정(아래 B-1).

## B. devenv-final이 미룬 것
1. **`CLAUDE.md` 8KB 이하(단계 34, 미달: 15,806바이트)**. 뜻을 바꾸지 않는 압축만으로는 닿지 않는다. 옮기면 줄어드는 후보(각각 "언제 읽히는가"가 달라지므로 사용자 결정):
   - §4 설계 원칙(2.5KB)과 §3 테스트 정책(0.9KB)을 `rules/`의 경로 한정 규칙으로: 코드를 읽거나 고칠 때만 읽힌다(계획 단계에서는 안 읽힌다).
   - `## Gotchas`·`## Release`(1.7KB, 이 저장소 전용인데 전역 파일이라 모든 프로젝트에서 읽힌다)를 저장소 문서로 옮기고 포인터만: 학습 루프(§2a)가 Gotchas를 `CLAUDE.md`에 적게 되어 있어 그 문장도 같이 바뀐다.
   - §1.6과 §2의 hook 내부 동작 설명(약 0.8KB)을 `hooks/retention.md`로.
   - 셋을 다 해도 약 9.9KB다. 8KB에는 규칙 문장 자체를 줄여야 닿는다.
2. **가드가 다루지 않는 입력(단계 16, 사용자 결정으로 미룸; 지금 판정은 허용이고 `tests/test_guard.py`에 그대로 적혀 있다)**: `set`, `export -p`, `python -c "print(os.environ)"`, `python -c "print(open('.e'+'nv').read())"`, `[Environment]::GetEnvironmentVariables()`, `$env:GITHUB_TOKEN`, `git add -A`, `~/.claude`에서의 `python -c "…listdir('state')"`와 `git grep`, `Read ~/.claude/history.jsonl`.
3. **pyright 오류 110건**(단계 7; hooks 15, tests 95; 1.1.414). 대부분 테스트가 동적으로 불러온 모듈과 `dict | None` 반환값을 그대로 쓰는 데서 나온다. 억제 주석 없이 0으로 만든 뒤 게이트 단계로.
4. **hook 지연 30% 단축 미달(단계 30)**: pre 408 → 약 325ms(−20%), post·prompt는 거의 그대로. 남은 것: 프로세스 시작과 import 약 115ms, post 안의 ruff 두 번 약 300ms, 상태 파일 열기 26~76ms(원인 미확인). `content_map`의 실행 내 메모는 하지 않았다(두 번 계산하는 경로가 있는지 미확인).
5. **CI 5분 이하(단계 31)**: 측정 불가(A의 첫 항목). 로컬은 wall 280초로 목표를 넘겼다. 더 줄일 후보: `test_guard`(약 115초, guard를 `-I` 없이 띄우는 형태라 `tests/inproc.py`로 옮기지 않았다), 실시간에 기대는 기준을 주입한 시계로(31의 c), `run_all.MEASURED_S`를 전환 뒤 값으로(지금은 시간 제한이 느슨하다).
6. **콘솔 인코딩 가드가 없는 테스트 모듈 11개**(`sys.stdout.reconfigure(errors="backslashreplace")`; 지금은 비 ASCII를 찍지 않아 통과). 같은 함정으로 CI가 세 번 실패했다 → `test_run_all`의 정적 검사로 고정할 후보.
7. 단계 26에서 남긴 것: `notify`가 상태 읽기만 import하게, 미선언 저장소의 증거를 저장소별 키로, 이번 턴 커밋 판정을 HEAD 조상 관계로.
8. PowerShell 도구에는 `cd <루트> &&` + 게이트 형태가 없다(루트에서 게이트만 실행하거나 Bash 도구를 쓴다, 단계 10).
9. 세션 중 정책 변경의 한계(단계 11): 새 세션은 바뀐 선언을 그대로 받는다; 옛 게이트를 더는 실행할 수 없는 정당한 변경은 그 세션에서 passed가 되지 않는다.
10. 병합 시점에 살아 있던 세션은 포매터를 한 번 두 번 돌리거나 낡은 증거를 한 번 볼 수 있다(단계 25).
11. ExitPlanMode의 PostToolUse가 승인 뒤에만 오는지(단계 27, headless로 잴 수 없었다: 미확인).
12. 보관된 계획 본문 안의 `docs/plans/…` 경로 표기는 옮기기 전 그대로다(단계 35; 기록이라 고치지 않았다).
13. **`discipline.py` 분할의 나머지(단계 40)**: 226KB 한 파일이 남아 있다(`hooks/planscope.py`로 38개 이름만 나갔다). 9개 구획이 서로를 쓰고(순환) 파일이 `__main__`으로 실행되므로, 글자 그대로 옮기는 방식은 여기까지다. 다음 순서의 안: (1) 경로 도우미(`absolute`, `is_unc`, `may_be_repository`, `_inside`)를 바닥 모듈로, (2) 상태를 읽고 판정만 돌려주는 함수(Stop 판정, 프롬프트 분류)를 `state`를 인자로 받는 순수 함수로 바꿔 옮기고, (3) `global`로 다시 묶는 4개 이름을 실행 문맥 객체 하나로, (4) discipline.py는 입력 해석과 이벤트 분기만. 이음매마다 게이트와 bench로 확인. 같이 할 것: `_inside` 중복, CI 판정 두 벌, 계획 순회 4곳.
14. `C:\dev\_iso_trial`: `.git` 밖에 `seed.txt`(5바이트)가 있어 지우지 않았다(단계 38). 필요 없으면 사용자가 지운다.
15. **마감 리뷰가 찾은 가드·판정의 남은 입력(2026-11-18; 리뷰어가 가드를 실행해 확인했다고 보고, 주 세션은 재현하지 않음)**. 심각도 순.
    → **2026-11-19 `guard-b15`(`docs/plans/2026-11-19-guard-b15.md`)로 처리.** 2026-11-19에 운영본 guard로 재생해 모두 실제로 뚫리는 것을 확인했고(미확인 해소), 아래 여섯 묶음을 닫았다: 보호 폴더(임시 폴더 훑기·`cd /tmp`·중괄호·`$env:APPDATA` 형태), 비밀 파일 글롭(실제 전개로 판정; `ls .env*`·`git add src/id_*`·`rg -g` 오차단 해소), 재귀 삭제(`rm -rf *`·`$PWD`·`cmd /c "…"`·PS 파이프라인·`-Recurse:$true`), 라이브 설정 ask(상대 경로·`cd` 패턴·git checkout/restore·skills/rules/settings.local.json), read-only subagent(git 허용 목록·포매터/설치/다운로드 거부·`$TEMP` 쓰기 허용), 그 밖(게이트 뒤 `;`·줄바꿈, `load_key` 원자적·없을 때만 생성, `MIN_KEY_BYTES` 32, PSScriptAnalyzer 1.25.0 고정). **남긴 것(그 계획의 비목표)**: 깨진 payload의 exit 0(하네스 입력), 인터프리터 `-c` 문자열의 `.env` 리터럴 오차단(완화하면 `open('.env')` 탐지가 사라짐), `format_lint`의 RUF100 오탐(재현 프로젝트 필요), `is_repo_root` 주석, `%TEMP%\claude-discipline`의 상태 파일 수(보호 폴더: 사용자가 자기 터미널에서 `state_gc`와 함께 확인). 원래 목록은 기록으로 그대로 둔다:
    - 보호 폴더: 임시 폴더 전체를 훑는 명령(`grep -r x /tmp`, `rg x $TEMP`, `tar -C <temp> .`, `Get-ChildItem -Recurse $env:TEMP`, Grep 도구의 `path`가 임시 폴더), `cd /tmp && cat claude-discipline/a`(cd 추적이 `/tmp`를 `C:\tmp`로 푼다), 중괄호 전개 `cat ~/.claude/{discipline,x}/key`, `$env:APPDATA\..\..\.claude\state\…`. 서명 키와 서명 없는 세션 상태가 걸려 있다.
    - 비밀 파일 글롭: `cat .e*`, `cat .e??`, `cat .{env,x}`, `base64 .en*`, PowerShell `Get-Content *env`(PowerShell 와일드카드는 점 파일도 맞춘다), `cat ~/.s*/id*`, `cat ~/.aws/cred*`. 제안: 이름 추정 대신 패턴을 실제로 전개해 판정.
    - 재귀 삭제: `rm -rf *`(cwd가 `C:\dev`나 홈), `rm -rf $PWD`, `cmd /c "rd /s /q C:\dev"`(따옴표로 한 단어), `Get-ChildItem C:\dev | Remove-Item -Recurse -Force`, `-Recurse:$true`.
    - 라이브 설정 쓰기 확인(ask)이 빠지는 경우: 세션 폴더가 `~/.claude` 안일 때의 상대 경로(`echo x >> hooks/guard.py`), `cd ~/.cl*/hooks && …`, `git -C ~/.claude checkout <ref> -- hooks`, `skills`·`rules`·`settings.local.json`.
    - read-only subagent에게 남은 쓰기: `git checkout-index -a -f`, `git read-tree`, `git -c alias.z=!… z`, `ruff format .`, `ruff check --fix .`, `npx prettier --write`, `uv sync`, `pnpm install`, `curl -o`, `mkdir`. 제안: git은 읽기 서브커맨드의 허용 목록으로.
    - 오차단: `ls .env*`, `ls src/credentials*`, `git add src/id_*`, `rg -g /**/*.py`, 인터프리터 `-c` 문자열 안의 리터럴.
    - 그 밖: 깨진 payload에서 guard가 exit 0(fail-closed의 예외), 줄바꿈이나 `;`로 끝나는 게이트 실행이 unknown, `format_lint`의 `--extend-ignore I001`이 RUF100을 켠 프로젝트에서 `# noqa: I001`을 오탐, `tests/inproc.py`가 `python -I`에 없는 `sys.path` 항목을 준다(지금 결함은 없음), CI의 `Install-Module PSScriptAnalyzer`에 버전 없음, `planscope.load_key`가 읽기 실패 때도 새 키를 비원자적으로 쓴다, `approvals.MIN_KEY_BYTES`(16)와 생성 길이(32)의 차이, `is_repo_root`의 주석("Asked from git")이 사실과 다를 수 있다, SubagentStart 안내의 "%TEMP% 아래에만"(변수로 적으면 거부된다), `%TEMP%\claude-discipline`에 상태 파일 2만 3천여 개(원인 미확인).

## C. 닫힌 계획에서 넘어온 것
- 마스터의 미체크 4개: 3(P0 단계 계획 작성: 실제로는 P0 계획들이 끝났다, 표기만 남음), 5(P0-04 승인 수명), 12(P0-11 메모리를 전역·프로젝트로 분리, `verified_at`·`source`; 메모리 이동은 별도 승인), 26(P3-01, 위 A).
- P4: P4-03 subagent 정의 정비, P4-04 subagent 실행·실패·완료 판정의 검증 보강, P4-05 skill을 실제 절차에 맞추기, P4-06 skill·agent 문서의 회귀 테스트, P4-08 MCP 사용 기준과 쓰지 않는 서버 처리(연결 해제는 별도 승인), P4-09 메모리 분리와 세션 시나리오 재확인, P4-10 마스터의 남은 것과 원인 미확인 2건, P4-11 강제 전환 준비. devenv-final의 단계 36~38이 P4-03·05·08의 일부를 다룬다(그 단계의 진행 기록 참조). P4-07(MCP 도구 권한 분류)은 `tests/test_mcp_permissions.py`가 고정하고 있다.
- P5: P5-01(위 A), P5-02 임의 실행 허용 좁히기(`uv:*`, `node:*` 등), P5-03(위 A), P5-04 세 agent의 `model: opus` 줄(상태 미확인: `agents/*.md`를 보고 판단), P5-06(위 A), P5-07 마스터·`CLAUDE.md` 정리(마스터는 이번에 요약으로 바꿨다), P5-10 읽기 전용 무인 루프(아래 D), P5-11 skill을 체크리스트와 결정적 단계로 나누는 안, P5-12 A2(agent 읽기 전용의 실제 강제: 묶음 M과 devenv-final 단계 37), P5-13 동결 기준, P5-14 `task:` 줄 없는 프롬프트에서 승인이 조용히 끊기는 것(devenv-final 단계 23의 `NO_TASK_WARNING`으로 알림은 생겼다).
- 이번에 끝난 것(참고): P5-05(`onFailure: "block"`, 단계 20), P5-08의 재측정(단계 29), P5-09의 계획 보관(단계 35), P5-13의 `C:\dev` 잔해 정리(단계 38).

## D. devenv-final의 비목표 (이유와 함께)
- 무인 루프(P5-10)와 주간 리포트 자동화: 프로젝트를 시작하기 전에 필요하지 않고, 읽기 전용 강제와 fail-closed가 먼저였다(이번에 그 둘을 했다).
- 커버리지 측정: hook을 프로세스로 띄우는 테스트라 잴 수 없었다. 이제 14개 모듈이 프로세스 안에서 hook을 실행하므로(`tests/inproc.py`) 측정이 가능해졌다: 다음 후보.
- eval-harness 채점 우회, pytest 이전, enforce 전환, 평가자 VM(P3-01), quant-research 수집기 복구, 실제 프로젝트 저장소의 코드 변경.

## 단계
(없음: 목록이다)
