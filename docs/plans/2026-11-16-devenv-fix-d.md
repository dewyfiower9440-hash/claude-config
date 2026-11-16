---
status: done
task: devenv-fix-d
---

# devenv 결함 개선 D: 비밀 파일의 "이름만 묻는" git 조회는 guard가 막지 않는다 (결함 8)

- 묶음 계획: `docs/plans/2026-11-16-devenv-final-check-fixes.md` 단계 5, P5 단계 2
- 저장 위치: `docs/plans/2026-11-16-devenv-fix-d.md`
- 승인 근거: 사용자 지시 2026-11-16 "드러남 결함 상세히 확인후 결함 개선하자". 묶음 계획이 "예외의 정확한 범위는 그 묶음의 계획에서 따로 승인받는다"고 했다: **아래 "예외의 범위"가 그 승인 대상이다.**

## 다시 제시하는 이유 (2026-11-16)
- 이 계획은 한 번 승인됐다(ExitPlanMode). 그런데 승인과 계획 파일 저장 사이에 백그라운드 명령의 완료 알림이 도착했고, hook이 그 순간 "승인된 계획 파일이 아직 docs/plans에 없음"으로 판단해 승인 기록을 버렸다(Stop hook의 안내: "중계 뒤 승인을 유지하지 못함"). 그 뒤의 `hooks/guard.py`, `tests/test_guard.py` 편집과 게이트 실행은 hook 기준으로는 승인 기록 없이 이뤄졌다(관측 모드라 막히지 않았고, 나는 Stop 때까지 알지 못했다).
- 지금까지 한 것(미커밋): 아래 설계대로의 구현, 테스트 사례 38개, 게이트 통과(37개 모듈, wall 460초), code-reviewer 1회 실행 중.
- **처음 계획과 달라진 점 한 가지**: 처음 계획은 `git show HEAD:.env`, `git cat-file -p HEAD:.env`, `git log -L1,5:.env`가 "지금도 차단된다"고 적었다. 테스트를 써 보니 **지금까지 허용되고 있었다**(`리비전:경로` 모양을 비밀 파일로 보지 못하는 기존 구멍). 이 계획은 그것을 막는 것을 포함한다(아래 "함께 막는 것").
- 별도로 남기는 결함: "승인 직후, 계획 파일을 저장하기 전에 알림이 오면 승인이 사라진다"(묶음 A의 유지 조건이 아직 묶이지 않은 승인을 버린다). 이 묶음에서는 고치지 않고 묶음 계획의 기록에 적는다.

## Context
보안 감사 subagent가 "비밀 파일이 git 이력에 들어간 적이 있는가"를 확인하려고 `git log … -- .env`를 실행했는데 guard가 "command touches a secret file (.env)"로 막았다. 감사의 기본 확인을 할 수 없다. 그 명령은 커밋 목록만 보여 주고 파일 내용은 출력하지 않는다.

## 확인한 사실
- `hooks/guard.py:476-487`: 명령의 인자를 낱말로 쪼개 `is_secret_path`에 맞는 낱말이 하나라도 있으면 막는다. 예외는 두 가지뿐이다: 커밋 메시지 같은 값 인자(`message_value_indexes`)와 검색 명령의 패턴 인자(`search_patterns`, `:120-160`).
- `SECRET_PATH_PATTERNS`(`:20-33`)에는 `.env`, `.env.*`, `*.pem`, `*.key`, `~/.ssh`, `.claude/discipline` 등이 있다. `.env.example` 등은 허용(`:34`).
- 파싱하지 못한 명령은 따로 정규식(`SECRET_COMMANDS`, `:37`, `:226-231`)으로 막는다: 이 경로는 건드리지 않는다.
- 기존 공격 코퍼스: `tests/test_guard_corpus.py`(약 150초).

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. git의 세 가지 조회만, 허용 옵션 목록으로(추천) | `git log`·`git ls-files`·`git check-ignore`에서, 아래에 적은 옵션만 쓰였고 파이프로 이어지지 않을 때만 경로 인자의 비밀 파일 검사를 건너뛴다 | 내용을 출력하는 옵션은 목록에 없으므로 새 옵션이 생겨도 기본은 차단이다 |
| B. 금지 옵션 목록으로 | `-p`, `-S`, `-G` 등이 없으면 통과 | 빠뜨린 옵션(`-L`, `--cc`, `--word-diff` …)이 구멍이 된다 |
| C. guard는 그대로, 감사 절차를 바꾼다 | subagent 정의에 `git log --all --name-only \| grep`처럼 이름을 인자에 쓰지 않는 방법을 적는다 | 정책 변경이 없다. 다만 다른 세션·다른 agent는 같은 벽에 다시 부딪힌다 |

## 예외의 범위 (승인 대상)
다음을 **모두** 만족하는 git 명령 하나에 한해, 그 명령의 인자에 있는 비밀 파일 이름을 "건드림"으로 보지 않는다.
1. 하위 명령이 `log`, `ls-files`, `check-ignore` 중 하나다.
2. 쓰인 옵션이 전부 아래 목록 안에 있다(목록 밖 옵션이 하나라도 있으면 지금처럼 막는다).
   - `log`: `--all`, `--oneline`, `--follow`, `--name-only`, `--name-status`, `--stat`, `--reverse`, `--no-merges`, `--full-history`, `--diff-filter=…`, `--format=…`·`--pretty=…`, `--date=…`, `--since=…`·`--until=…`·`--after=…`·`--before=…`, `-n N`·`-N`·`--max-count=…`, `--`
   - `ls-files`: `--error-unmatch`, `-c`·`--cached`, `-o`·`--others`, `-i`·`--ignored`, `--exclude-standard`, `-z`, `--`
   - `check-ignore`: `-v`·`--verbose`, `-q`·`--quiet`, `-n`·`--non-matching`, `--no-index`, `--`
3. 그 명령의 출력이 다른 명령으로 이어지지 않는다(파이프 `|`의 왼쪽이 아니다). 출력 리다이렉션이 없다.
4. git에 `-c`, `--exec-path`, `-C` 외 전역 옵션이나 별칭 실행(`!`)이 없다. (`-C <dir>`는 허용: 다른 폴더의 저장소를 묻는 것.)

5. git을 직접 부른 경우만이다(`VAR=값` 접두나 `env`·`sudo` 같은 감싸는 명령이 없다). 인자에 `$`, 백틱, `<`, `>`가 없다. `$( )` 같은 중첩 안이 아니다.

막는 것은 그대로다: `git diff`, `git log -p`·`-S`·`-G`·`--cc`, `git grep`의 경로 인자, `git add -f`, 그리고 git이 아닌 모든 명령(`cat .env`, `type .env`, `Get-Content .env` …). Read·Edit·Grep 도구의 비밀 파일 차단(`:528-535`)과 `permissions.deny`도 그대로다.

### 함께 막는 것 (기존 구멍, 정책을 조이는 쪽)
git 명령의 인자에서 마지막 `:` 뒤가 비밀 파일 경로면 막는다: `git show HEAD:.env`, `git cat-file -p HEAD:.env`, `git log -L1,5:.env`. 지금까지는 허용됐다.

이 예외로 새로 알 수 있게 되는 것: 비밀 파일이 추적되는지, 무시되는지, 어느 커밋이 건드렸는지(해시·날짜·작성자·제목). 파일의 내용은 아니다.

## 변경 범위
- `hooks/guard.py`
- `tests/test_guard_corpus.py`
- `tests/test_guard.py`
- `docs/plans/**`

## 테스트 (먼저 작성해 실패 확인)
| 사례 | 기대 |
|---|---|
| `git log --all --oneline -- .env`, `git log --follow --name-status -- config/.env.local`, `git ls-files --error-unmatch .env`, `git check-ignore -v .env`, `git -C sub log --oneline -- id_rsa.pem` (bash와 PowerShell 양쪽) | 통과(지금은 차단) |
| 최종 확인의 실제 명령 모양: `git log --all --oneline -- .mcp.json .env .env.example` | 통과 |
| `git log -p -- .env`, `git log -S token -- .env`, `-G`, `-L1,5:.env`, `--cc`, 목록에 없는 옵션(`--word-diff`, `--output=x`) | 차단 |
| `git log --oneline -- .env \| cat`, `git ls-files .env \| xargs cat`, `git log -- .env > out.txt` | 차단 |
| `git show HEAD:.env`, `git diff -- .env`, `git cat-file -p HEAD:.env`, `git grep x -- .env`, `git -c alias.x='!cat .env' x` | 차단(지금과 같음) |
| `git log --oneline -- .env; cat .env`, `git log --oneline -- .env && type .env`, `$(cat .env)`가 든 인자 | 차단(뒤 명령·치환은 따로 검사된다) |
| `cat .env`, `Get-Content .env`, Read 도구의 `.env` | 차단(지금과 같음) |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-fix-d.md`에 저장한다(첫 편집 전에).
- [x] 2. 테스트를 쓰고 통과 사례가 지금 차단됨(실패)을 확인한다.
- [x] 3. 구현.
- [x] 4. 게이트(다른 세션의 게이트가 돌고 있지 않은지 먼저 확인).
- [x] 5. code-reviewer 1회: "이 예외로 비밀 파일의 내용을 읽을 길이 생기는가"만 집중해서 보게 한다. 반영, 게이트.
- [x] 6. 커밋, push, CI 확인, 마감 기록.

## 검증
- `python -I tests/run_all.py` 통과(끝줄의 wall 기록), CI 성공(run id). 상태 목표: CI_VERIFIED

## 위험
- 보안 규칙의 완화다. 막는 장치: 허용 목록 방식, 파이프·리다이렉션 금지, 하위 명령 세 개로 한정, 공격 사례를 통과 사례보다 많이 둔다.
- `--format=…`에 들어가는 자리표시자는 커밋 정보만 낸다(파일 내용을 내는 자리표시자는 없다). 리뷰에서 다시 확인하게 한다.
- git 별칭이 `log`를 다른 뜻으로 바꿔 둔 저장소: git은 내장 명령 이름의 별칭을 무시한다(문서로 확인 후 진행, 아니면 멈춘다).
- 게이트 시간: `test_guard_corpus`는 150초로 임계 경로가 아니다. 사례는 30개 안쪽으로 한다.

## 진행 기록
- 2026-11-16 단계 2: 통과시켜야 할 10건이 고치기 전에 차단됨(실패)을 확인했다. 같은 실행에서 "차단돼야 하는데 지금 허용되는" 3건이 드러났다: `git log -L1,5:.env`, `git show HEAD:.env`, `git cat-file -p HEAD:.env`. 처음 계획의 전제("지금도 차단")가 틀렸다. 다시 제시한 계획에 "함께 막는 것"으로 넣어 승인받았다.
- 단계 3: `hooks/guard.py`에 `GIT_NAME_QUERIES`, `git_names_only`. 비밀 경로 검사에 git의 `리비전:경로` 꼴 추가.
- 승인 기록: 첫 승인은 계획 파일을 저장하기 전에 도착한 백그라운드 완료 알림 때문에 hook 기록에서 사라졌다(Stop hook: "중계 뒤 승인을 유지하지 못함: 승인된 계획 파일이 아직 docs/plans에 없음"). 그 뒤의 첫 구현 편집과 게이트 1회는 hook 기준 승인 기록 없이 이뤄졌다(관측 모드라 통과, Stop 때 알았다). 계획을 다시 제시해 재승인받았으나, 그때도 리뷰 결과 알림이 저장 전에 도착했다. `python -I hooks/ledger.py show --task devenv-fix-d`가 "기록 없음"인 것으로 보아 재승인도 hook 기록에는 남지 않은 것으로 보인다(추정). 사용자의 승인 자체는 두 번 모두 ExitPlanMode로 있었다.
- 단계 5 리뷰(code-reviewer 1회, "ship", 탐침 약 75건): 이 예외로 비밀 파일의 내용을 읽을 길은 찾지 못했다. `rev:path` 검사의 오탐도 찾지 못했다(`git@host:repo.git`, `--format=%h:%s` 통과). 반영 2건: (1) 예외가 참이면 `rev:path` 검사까지 건너뛰어 `git log HEAD:.env`가 허용되던 것(내용은 나오지 않으나 계획 문구와 어긋남) → 위치 인자에 `:`가 있으면 예외를 주지 않는다, (2) 빠진 사례 25건 추가(축약형 `--pat`, 결합 플래그 `-1p`, 전역 옵션 `-p`·`--git-dir`·`--exec-path`, `env git`, `|&`, `2>&1`, PowerShell `*>`·`Tee-Object`, 둘째 줄 명령, `rev:path` 변형, 오탐 회귀).
- 리뷰가 범위 밖으로 남긴 한계(별도 제안): 경로 이름을 쓰지 않는 `git show HEAD`, 경로 없는 `git log -p`, `git cat-file -p <blob sha>`, `git archive HEAD`는 원래부터 허용된다(이름 기반 guard의 한계). 저장소 설정의 textconv·pager로 프로그램이 도는 길도 이 변경 이전부터 있다.
- 확인하지 않은 것: 기존 구멍(`git show HEAD:.env` 등)으로 과거에 비밀이 읽힌 적이 있는지.
- 게이트: `python -I tests/run_all.py` 통과 2회(wall 460초, 470초; 37개 모듈, 실제 명령 재생 코퍼스 `test_guard_corpus` 포함).
- 커밋 `5e9dc8b`, CI run 43837559360 성공(job hooks, 1차). 상태: CI_VERIFIED.
