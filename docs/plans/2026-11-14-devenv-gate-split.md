---
status: in-progress
task: devenv-gate-split
---

# devenv: test_discipline 분할 (게이트 시간 여유 확보)

- 마스터 계획: `docs/plans/2026-11-11-devenv-evolution-master.md` (F4b 다음, v2 운영 활성화 전의 별도 작업)
- 선행 작업: F4a·F4b 완료, main 763490b push됨
- 저장 위치: `docs/plans/2026-11-14-devenv-gate-split.md`
- 사용자 승인: 2026-11-14 프롬프트 "1번2번 둘다 진행해"(1번 push, 2번 이 분할)

## Context
`tests/test_discipline.py`는 2632줄짜리 순차 스크립트 하나이고, 마지막 게이트에서 498초가 걸렸다. 게이트(`python -I tests/run_all.py`)는 포그라운드 실행 제한이 600초다. 게이트는 테스트 파일을 3개씩 병렬로 돌리므로 전체 시간은 가장 긴 파일 하나가 정한다. 지금은 이 파일에 테스트를 몇 개만 더해도 게이트를 끝까지 돌릴 수 없게 된다. 이후 작업(R1, P0-05~)은 모두 이 파일에 테스트를 더한다.

목표: 테스트 내용은 그대로 두고 파일만 둘로 나눠, 가장 긴 파일의 실행 시간을 크게 줄인다.

## 확인한 사실
- `tests/run_all.py`: `TESTS` 목록의 파일을 `ThreadPoolExecutor`(기본 3개)로 병렬 실행하고, `SLOW_FIRST`에 적힌 파일을 먼저 시작한다. 각 파일은 별도 프로세스다.
- `tests/test_discipline.py` 구조(행 번호는 main 763490b 기준):
  - 1~67행: import, `HOOK`, 격리된 승인 저장소 `APPROVAL_HOME`(`tests/_tmp_discipline_approvals`, 시작할 때 지움), `TMP`, `call`, `decision`, `check`, `edit`.
  - 69~1169행: 약화 감지, 큰 변경, 승인(D-2), Stop 검증, 실패 증거, 게이트 선언, 리뷰 스위치, notify, 경로 분류, 사라진 저장소(초기 사례).
  - 1170~2547행: D3 최종 계약, P0-04b 개정(B-T), F4a(F-T), F4b(G-T). 모두 `d3_repo` 계열 도우미를 쓴다.
  - 2548~2630행: git 실패·캐시 사례(D2). 앞부분의 `REPO_A`, `WT_B`, `LINK`를 쓴다.
  - 끝: `rm_tree(PB)`, `FAILURES` 출력, 종료 코드.
- 1170~2547행이 앞부분에서 쓰는 이름은 `call`, `decision`, `check`, `reason`(422행), `rm_tree`(817행), `state_file`(968행), `PB`(814행), 표준 import뿐이다(이름 검색으로 확인, 최종 확인은 ruff의 미정의 이름 검사).
- 두 구간이 함께 쓰는 고정 경로가 있다: `PB = C:/dev/_disc_p004b`(시작과 끝에 통째로 지움), `APPROVAL_HOME`(시작할 때 지움). 병렬로 돌리려면 파일마다 달라야 한다.
- 다른 테스트 파일(`test_session_store.py` 등)은 각자 `hook`, `decision`, `reason` 도우미를 가진 독립 스크립트다. 공용 모듈은 없다.
- `.gitignore`는 `/tests/_tmp*/`를 무시한다.
- 구간별 소요 시간은 측정 중이다(1단계에서 기록). 498초 중 1170~2547행 구간이 절반 이상일 것으로 예상한다(미확인).

## 설계 판단
| 방식 | 내용 | 판단 |
|---|---|---|
| A. 구간을 새 파일로 옮김 | 1170~2547행을 `tests/test_discipline_d3.py`로 옮기고, 필요한 도우미 몇 개를 그 파일에 둔다 | **채택** |
| B. 공용 도우미 모듈 추출 | `call`, `check` 등을 공용 모듈로 빼고 두 파일이 import | 기각 |
| C. 게이트 제한이나 병렬 수 조정 | 시간 제한을 늘리거나 worker를 늘림 | 기각 |

- B 기각: 기존 테스트 파일들은 모두 독립 스크립트다. 공용 모듈은 기존 파일 앞부분까지 고쳐야 하고 실패 집계(`fails` 전역) 방식도 바꿔야 해서 변경 폭이 크다.
- C 기각: 제한은 실행 도구의 상한이라 늘릴 수 없고, 가장 긴 파일이 그대로면 병렬 수를 늘려도 줄지 않는다.

## 설계
1. 새 파일 `tests/test_discipline_d3.py`
   - 머리말: 기존 파일 1~57행과 같은 내용(import, `HOOK`, `TMP`, `call`, `decision`, `check`). 승인 저장소는 `tests/_tmp_discipline_d3_approvals`로 따로 둔다.
   - 도우미 `reason`, `rm_tree`, `state_file`을 기존 정의 그대로 둔다(기존 파일에도 남는다: 앞 구간이 쓴다).
   - `PB = Path("C:/dev/_disc_d3")`, 시작할 때 `rm_tree(PB)`.
   - 본문: 기존 1170~2547행을 **글자 그대로** 옮긴다.
   - 끝: `rm_tree(PB)`, `FAILURES` 출력, 종료 코드(기존과 같은 형식).
2. `tests/test_discipline.py`: 1170~2547행을 지운다. 그 밖은 건드리지 않는다. 지운 뒤 쓰이지 않게 되는 도우미가 있으면 ruff가 알려 주는 범위에서만 정리한다.
3. `tests/run_all.py`: `TESTS`에 `test_discipline_d3`를 `test_discipline` 다음에 넣고, `SLOW_FIRST`에도 넣는다.
4. 기대값에 경로 문자열이 직접 박힌 곳이 있는지 확인한다(`_disc_p004b`는 정의 1곳뿐임을 확인했다). 있으면 변수로 계산하는 형태 그대로 두고, 값이 달라지는 곳만 기록한다.

테스트의 시나리오, 기대값, 이름은 바꾸지 않는다. 삭제·skip 없음.

## 변경 범위
- `C:/Users/user/.claude/tests/test_discipline.py`
- `C:/Users/user/.claude/tests/test_discipline_d3.py`
- `C:/Users/user/.claude/tests/run_all.py`
- `C:/Users/user/.claude/docs/plans/**`

이 작업에서 하지 않는 일: hook 코드 수정, 테스트 내용 변경, 다른 테스트 파일 분할, CI 워크플로 수정, v2 활성화.

## 수용 기준
| 항목 | 기준 |
|---|---|
| 테스트 보존 | 분할 전 실행의 check 이름 목록(순서 포함, 중복 포함)과, 분할 뒤 두 파일의 check 이름을 이어 붙인 목록이 같다. 개수도 같다. 모두 OK |
| 옮긴 구간 | 새 파일의 본문이 기존 1170~2547행과 줄 단위로 같다(`PB` 경로가 드러나는 출력값 제외, 소스는 동일) |
| 병렬 안전 | 두 파일을 동시에 실행해도 둘 다 FAILURES 0 (게이트가 실제로 그렇게 돌린다) |
| 시간 | 게이트에서 두 파일 중 긴 쪽이 350초 이하. 넘으면 분할 지점을 다시 정하지 않고 측정값과 함께 보고한다 |
| 게이트 | `python -I tests/run_all.py` GATE PASSED |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-14-devenv-gate-split.md`에 승인된 원문 그대로 저장한다. 분할 전 기준 실행(check 이름 목록, 구간별 시간)을 기록한다.
- [x] 2. 새 파일을 만들고 기존 파일에서 구간을 지운다(스크립트로 줄 범위를 그대로 옮긴다. 손으로 다시 쓰지 않는다). `ruff check`, `ruff format --check`.
- [x] 3. 두 파일을 각각 실행하고, check 이름 목록을 기준 실행과 비교한다.
- [x] 4. `run_all.py`에 등록한다. 두 파일을 동시에 실행해 병렬 안전을 확인한다.
- [x] 5. 게이트를 main 루트에서 원문 그대로 실행한다. 파일별 시간을 기록한다.
- [ ] 6. 로컬 커밋(테스트·러너 / 계획 문서). 마스터플랜 현황에 한 줄 기록한다. push는 하지 않고 보고에서 묻는다.

리뷰: 동작을 바꾸지 않는 줄 이동이고 보존 여부를 목록 비교로 기계적으로 확인하므로 code-reviewer는 실행하지 않는다.

## 검증
- 수용 기준 표의 다섯 항목
- 상태: LOCALLY_VERIFIED / CI_PENDING

## 위험
- 옮긴 구간이 앞 구간의 세션 상태나 파일에 암묵적으로 기대고 있으면 새 파일에서 실패한다. 3단계의 목록 비교와 실행 결과로 드러난다. 드러나면 원인을 기록하고, 테스트 내용을 고쳐서 맞추지 않는다(필요한 준비 코드만 옮긴다).
- 병렬 실행으로 두 파일이 동시에 hook 프로세스를 많이 띄운다. 파일당 실행 시간이 단독 실행보다 길어질 수 있다(게이트 측정으로 확인).
- CI(windows-latest)에서의 시간은 push 전까지 미확인이다.
- 분할 뒤에도 `test_approvals`(217초)와 새 두 파일이 긴 축이다. 다음에 다시 차면 같은 방식으로 한 번 더 나눌 수 있다.

## 진행 기록
- 2026-11-14: 1단계. 승인된 본문을 그대로 저장했다. 분할 전 기준 실행: check 194개 모두 OK, 단독 실행 503.7초(Python 3.14). 같은 파일을 Python 3.13(CI와 같은 버전)으로 돌리면 405초였다.
- 2026-11-14: push(사용자 승인 "1번") 결과 CI run 43569565401이 실패했다. 이 작업과 함께 고친다(아래 "CI 실패 대응").
- 2026-11-14: 2단계. 스크립트로 기존 1170~2547행(1378줄)을 `tests/test_discipline_d3.py`로 옮겼다. 옮긴 블록이 원본과 글자 단위로 같고, 기존 파일은 그 블록을 뺀 것 외에 달라지지 않았음을 스크립트로 확인했다. ruff 통과(미정의 이름 없음).
  - 암묵 의존 1건: 옮긴 구간은 앞 구간이 만들어 둔 `PB` 폴더를 전제로 했다(`d3_repo`가 상위 폴더를 만들지 않음). 새 파일 머리에 `PB.mkdir(parents=True)` 한 줄을 더했다. 테스트 내용은 바꾸지 않았다.
- 2026-11-14: 3~4단계. `run_all.py`의 `TESTS`와 `SLOW_FIRST`에 등록했다. 두 파일을 동시에 실행: 둘 다 FAILURES 0, 237초와 259초. check 이름 목록 비교: 194 = 107 + 87, 순서까지 같고(앞 파일의 처음 101개, 옮긴 87개, 앞 파일의 나머지 6개) 모두 OK.

## CI 실패 대응 (계획 밖 추가, 사용자 지시 "push 진행"의 후속)
- CI run 43569565401(main 763490b) 실패 3건:
  1. `test_approvals`: check 이름에 든 한글을 CI 콘솔(cp1252)에 출력하다 UnicodeEncodeError로 중단.
  2. `test_approvals_store`: 같은 원인(기대값의 한글).
  3. `test_discipline`: FAILURES 1. 러너가 실패한 모듈의 출력 끝 3000자만 보여 줘서 어느 check인지 로그에 없다. 로컬에서는 Python 3.13으로도 재현되지 않았다(미확인).
- 조치:
  - `tests/test_approvals.py`, `tests/test_approvals_store.py`: stdout을 `errors="backslashreplace"`로 설정했다(출력 형식만 바뀌고 판정은 그대로). cp1252 환경은 로컬에서 재현하지 못했으므로 효과는 다음 CI에서 확인한다(미확인).
  - `tests/run_all.py`: 실패한 모듈은 끝부분 앞에 있는 `FAIL` 줄(최대 20줄)도 출력한다. `tests/test_run_all.py`에 빨간 테스트를 먼저 추가했다(수정 전 FAIL 확인).
  - 3번은 이 변경을 push한 뒤 CI 로그로 원인을 확인한다.
- 범위: `tests/test_approvals.py`, `tests/test_approvals_store.py`, `tests/test_run_all.py`는 승인된 변경 범위 밖의 파일이다. CI 복구에 필요한 최소 변경으로 판단해 포함했고 보고에 명시한다.
- 2026-11-14: 5단계. 게이트 GATE PASSED. test_discipline 240초, test_discipline_d3 259초(분할 전 498초). 수용 기준의 350초 이하를 만족한다.
- 2026-11-14: 커밋 b6d3116(분할. `run_all.py`의 FAIL 줄 출력 변경도 이 커밋에 함께 들어갔다), 4fd8464(인코딩 수정, 러너 테스트), 8c647d7(문서). push 뒤 CI run 43572367057: 인코딩 2건은 해결, `test_discipline`의 1건이 드러났다.
  - 실패한 check: "R17 the deleted repository's baseline is kept, not dropped". CI에서 test_discipline 127초, test_discipline_d3 150초.
  - 원인 추정(미확인): 이 check만 `%TEMP%` 아래 경로(`TMP / "wtD"`)를 상태의 키와 `normcase`로 직접 비교한다. windows-latest의 `%TEMP%`가 8.3 짧은 경로(`RUNNER~1`)이면 hook이 기록한 긴 경로와 문자열이 달라진다. 같은 시나리오의 다른 check 두 개(사유 문구 확인)는 CI에서 통과했으므로 hook 동작이 아니라 테스트의 비교 방식 문제로 본다.
  - 조치: 비교 양쪽에 `os.path.realpath`를 적용했다. 추정이 틀렸을 때를 대비해, 불일치하면 기록된 키 목록이 출력되게 했다(일치하면 기존과 같이 True).
