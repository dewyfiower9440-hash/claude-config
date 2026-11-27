---
status: done
task: format-lint-fix-only
---

# format_lint: 프로젝트의 `fix-only = true`가 Stop의 검사를 "고치기"로 바꾸는 것을 막기

- 저장 위치(승인 뒤): `C:\dev\_wt\claude-config\docs\plans\2026-11-27-format-lint-fix-only.md`
- 배경: 오늘 릴리스한 `c80-followup`(커밋 `06bbdc0`)은 턴이 끝날 때의 ruff 검사가 파일을 고치지 않게 하려고 `--no-fix`를 붙였다. 릴리스 뒤 코드 리뷰가 빠진 경우를 재현했다: 프로젝트의 ruff 설정에 `fix-only = true`가 있으면 `--no-fix`로는 막히지 않아 파일이 고쳐지고, 남은 오류(F821)도 보고되지 않는다. 지금 `C:\dev` 아래의 ruff 설정에는 `fix-only`가 없어(검색 0건) 당장 영향받는 프로젝트는 없다.
- 릴리스와 push: **이 계획의 승인은 릴리스와 push까지 포함한다.** hook 파일이 바뀌므로 hook 지연 측정을 거치고, `merge --ff-only` 뒤 `git push origin main`, CI를 끝까지 따라간다. 강제 push는 하지 않는다.

## 목표
ruff 설정이 무엇이든 (1) Stop의 검사는 파일을 고치지 않고 남은 문제를 보고한다. (2) 편집 직후의 보고용 검사도 설정 때문에 고치기로 바뀌거나 조용해지지 않는다. 비목표: `.cmd` 래퍼일 때의 시간 한도(리뷰의 추정 지적, 재현되지 않음), 그 밖의 hook 변경, 테스트 공백(예산 계산) 메우기.

## 조사 결과
- `hooks/format_lint.py:313-324` (`deferred_problems`): `ruff check --no-fix --no-cache --output-format concise <files>`. 리뷰어의 재현(ruff 0.17.3): `[tool.ruff]\nfix-only = true`와 `import os\n\nx = undefined_name\n`에 같은 인자를 주면 "Fixed 1 error.", 종료 코드 0, `import os`가 지워지고 F821은 출력되지 않음. `--no-fix-only`를 더하면 파일이 그대로이고 F401·F821이 보고되며 종료 코드 1.
- `ruff check --help`(0.17.3): "`--fix-only` … Implies `--fix`. Use `--no-fix-only` to disable". 플래그가 이 버전에 있다.
- `hooks/format_lint.py:153-164` (`python_file`, 편집 직후): 고치기(`--fix --unfixable …`)와 format 뒤에, 플래그 없이 `ruff check --extend-ignore … --output-format concise <file>`로 남은 문제를 보고한다. 같은 원인으로 설정의 `fix = true`나 `fix-only = true`를 그대로 따를 것으로 보인다: `fix-only`면 보고가 비고, `fix = true`면 151행이 `--unfixable`로 남겨 둔 것까지 고칠 수 있다. **읽은 것이고 실행으로 확인하지 않았다(미확인)**: 2단계의 테스트로 먼저 확인한다.
- 테스트: `tests/test_format_lint.py:409-419`의 F-T16이 `fix = true`만 다룬다(`write`, `turn_with`, `hook("stop", …)` 도우미 사용). `fix-only` 경우가 없다.
- 최상위 계획 파일은 10개로 상한이다: 닫힌 계획 하나를 `archive/`로 옮긴다.
- 작업 트리의 `docs/plans/2026-11-24-monitor-v1-2.md`는 다른 세션의 미커밋 변경이다: 건드리지 않는다.

## 변경 범위
- C:/dev/_wt/claude-config/hooks/format_lint.py
- C:/dev/_wt/claude-config/tests/test_format_lint.py
- C:/dev/_wt/claude-config/tests/_baseline/**
- C:/dev/_wt/claude-config/docs/plans/**

## 가정
- 편집 직후의 보고용 검사(153-164행)가 실제로 설정을 따르는지는 테스트가 정한다. 테스트가 지금 코드에서 통과하면(문제가 없으면) 그 부분의 코드는 고치지 않고 테스트만 남기며, 그렇게 보고한다.
- 편집과 계획서 기록을 모두 끝낸 뒤에 리뷰어를 돌린다. 리뷰가 범위 밖의 수정을 요구하면 고치지 않고 보고한다.

## 선택한 접근
두 호출에 플래그를 명시한다: Stop의 검사에 `--no-fix-only`, 편집 직후의 보고용 검사에 `--no-fix`와 `--no-fix-only`. 버린 안: `--isolated`로 프로젝트 설정을 통째로 무시 — 프로젝트가 끈 규칙까지 판정하게 되어, 이 함수가 일부러 지키는 "파일 자신의 ruff 설정으로 판정"을 깬다.

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-27-format-lint-fix-only.md`에 저장(`status: in-progress`). 닫힌 계획 `2026-11-19-devenv-v1-1.md`를 `git mv`로 `archive/`에 옮긴다 — 검증: 승인본과 status 줄만 다름, 최상위 10개.
- [x] 2. 테스트 먼저(`tests/test_format_lint.py`, F-T16 다음): (가) `fix-only = true`인 프로젝트에서 Stop 뒤 파일이 그대로이고 F401·F821이 둘 다 이름 붙여 보고됨. (나) `fix-only = true`인 프로젝트에서 편집 직후 hook이 남은 문제를 보고함(조용해지지 않음). (다) `fix = true`인 프로젝트에서 편집 직후 hook이 `UNFIXABLE`로 남겨 둔 것을 고치지 않음 — 검증: `python -I tests/test_format_lint.py`에서 (가)가 지금 코드로 실패함을 확인. (나)(다)는 실패하면 3단계에서 고치고, 통과하면 그대로 둔다.
- [x] 3. `hooks/format_lint.py`: `deferred_problems`의 인자에 `--no-fix-only` 추가(docstring의 "never fixed" 설명에 `fix-only` 한 구절). 2단계에서 (나)나 (다)가 실패했으면 `python_file`의 보고용 검사에 `--no-fix`, `--no-fix-only` 추가 — 검증: 같은 테스트 통과.
- [x] 4. 게이트 `python -I tests/run_all.py`. hook 지연 측정(`<hook 인터프리터> -I tests/bench_hooks.py format-lint-fix-only`)을 앞 기준(`2026-11-27-c80-followup-recall.json`)과 비교하고, 게이트를 다시 실행 — 검증: `GATE PASSED`, 20% 넘게 느려진 hook 없음, 승인 사례 결과 같음.
- [x] 5. 진행 기록을 쓰고 마지막 편집으로 `status: done`.
- [ ] 6. `code-reviewer`에게 미커밋 변경을 보임 → ship이면 커밋(수정과 테스트 / 기준 파일 / 계획서와 보관 이동) → 릴리스(`merge --ff-only`, 실제 hook 확인, `~/.claude`에서 게이트) → 나가는 변경분 훑기 → `git push origin main` → CI 확인 — 검증: CI run id와 결과.

## 리스크와 대응
- ruff가 오래된 버전이라 `--no-fix-only`를 모르면 종료 코드 2로 끝난다 → 그 경우는 이미 "ruff failed"로 건너뛰고 기록한다(`:327` 이하). 차단은 사라지지만 hook은 죽지 않는다. CI의 ruff 버전에서 테스트가 통과하는지는 CI가 보여 준다.
- 편집 직후의 보고가 달라지면(다) 지금까지 조용히 고쳐지던 것이 보고로 바뀔 수 있다 → `fix = true`를 쓰는 프로젝트에서만이고, `C:\dev`에는 지금 없다(미확인: 2단계에서 `fix =`도 검색해 적는다).
- 실제 hook이 잠기면: 릴리스 절차의 복구 방법(`git -C ~/.claude checkout <마지막 정상 커밋> -- hooks settings.json`, 마지막 정상 커밋은 `f764618`).
- `~/.claude/settings.json`의 미커밋 변경(모델 값)은 그대로 둔다. 이번 커밋은 그 파일을 건드리지 않는다.

## 영향 범위
`hooks/format_lint.py`의 ruff 호출 한두 곳, 그 테스트, 지연 기준 파일 하나. 다른 hook·규칙·스킬은 바뀌지 않는다.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-27 단계 1: 계획 저장, 닫힌 계획 `2026-11-19-devenv-v1-1.md`를 `archive/`로 옮김. 어긋난 점: 저장한 뒤 `status` 줄을 `in-progress`로 바꾸는 것을 빠뜨려 첫 테스트 편집이 hook에 막혔다. 그 줄만 바꾸고 이어감.
- 2026-11-27 단계 2(테스트 먼저): 처음 쓴 (가)는 설정을 하위 폴더(`fixonly/pyproject.toml`)에 두었고 지금 코드에서 **통과**했다. 원인을 명령줄로 확인: ruff는 `fix`·`fix-only`를 자신이 실행되는 폴더의 설정에서만 읽고, Stop은 저장소 루트에서 ruff를 실행한다(하위 폴더의 `fix = true`를 부모 폴더에서 플래그 없이 돌리면 파일이 그대로이고 종료 코드 1). 따라서 기존 F-T16(`fixing/` 하위 폴더의 `fix = true`)은 `--no-fix`가 있든 없든 통과하는, 실패할 수 없는 검사였다. (가)를 루트에 설정을 둔 저장소 둘(`fix = true`, `fix-only = true`)로 다시 쓰고 `fl.deferred_problems`를 직접 호출하게 함(F-T19). 결과: `fix = true`는 통과(기존 `--no-fix`가 막음), `fix-only = true`는 실패(`'\nx = undefined_name\n'`, F401·F821 보고 없음) — 리뷰어의 재현과 같음. (나) F-T20은 실패(종료 코드 0, F601 보고 없음): 편집 직후의 보고도 `fix-only`에서 사라진다는 것이 확인됨. (다) F-T21은 지금 코드에서 통과(보고용 검사가 F401을 무시 목록에 두어 고치지 않음): 그대로 둠. `C:\dev` 아래 ruff 설정에서 `fix =`·`fix-only =` 검색 0건.
- 2026-11-27 단계 3: `hooks/format_lint.py` — `deferred_problems`의 인자에 `--no-fix-only`, `python_file`의 보고용 검사에 `--no-fix`와 `--no-fix-only`(F-T20이 실패했으므로), docstring 한 구절. `python -I tests/test_format_lint.py` FAILURES: 0. F-T16은 고치지 않고 두었다(F-T19가 그 뜻을 실제로 검사한다).
- 2026-11-27 단계 4: 게이트 `python -I tests/run_all.py` → 45개 모듈, 2692개 검사, `GATE PASSED`. hook 지연: 앞 기준(오늘 아침, `2026-11-27-c80-followup-recall.json`)과 비교하면 모든 hook이 +19~+47%로 나왔으나, 이 변경이 닿지 않는 `guard`와 `format_lint(.md)`도 같이 느려졌고 그때 기계에 다른 python 프로세스 12개와 CPU 부하 24%가 있었다(게이트도 334초 → 385초). 같은 조건에서 다시 잼: 릴리스된 커밋 `f764618`의 임시 체크아웃과 이 변경을 번갈아 두 번씩(b1, a1, b2, a2). p50의 변화는 최솟값끼리 −12.7%~+6.8%, 평균끼리 −8.9%~+3.1%로 20%를 넘게 느려진 hook이 없고, 승인 사례 결과도 같다. 계획과 달라진 점: 부하가 걸린 상태의 측정값을 새 기준으로 남기면 다음 비교가 느슨해지므로 기준 파일을 추가하지 않았다(측정 파일과 임시 체크아웃은 지움). 파일이 추가되지 않아 내용이 게이트 때와 같으므로 게이트를 다시 돌리지 않았다(`~/.claude`에 반영한 뒤 한 번 더 돈다).
- 2026-11-27 단계 5: 기록을 쓰고 닫음. 6단계(리뷰, 커밋, 릴리스, push, CI)는 `done` 뒤의 일이라 최종 보고에 적는다.
