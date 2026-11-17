---
status: done
task: devenv-gate-split
---

# devenv: 게이트 시간의 여유를 되찾는다 (`test_approvals` 분할과 동시 실행 수)

- 저장 위치: `docs/plans/2026-11-17-devenv-gate-split.md`
- 승인 근거(사용자 결정 2026-11-17, 그대로 인용):
  - "게이트 여유 90초: P5-08의 test_approvals 분할을 묶음 H 다음으로 당긴다."
  - "분할은 내용 변경 없이 모듈만 나누고, 기존 테스트 수가 그대로인지 확인한다."
- push: 이 저장소의 커밋은 push하고 CI를 끝까지 따라간다(사용자 결정 2026-11-16 "push 허용: 각 작업의 커밋은 push하고 CI를 끝까지 따라간다").

## Context
게이트(`python -I tests/run_all.py`)는 600초 안에 포그라운드로 끝나야 증거가 된다. 오늘 wall이 549 → 564 → 566초였다: 여유 34초. 한 번 넘으면 그 뒤의 모든 작업이 검증에서 막힌다.

## 확인한 사실 (2026-11-17 측정)
- 지금: `gate: 37 modules, sum 2059s, wall 566s, workers 4`. 가장 긴 모듈은 `test_approvals` 423초.
- wall은 가장 긴 모듈이 아니라 **합계 ÷ 동시 실행 수**(2,059 ÷ 4 = 515초)에 묶여 있다. 이 상태에서는 모듈을 나눠도 합계가 같아 거의 줄지 않는다.
- 동시 실행 6개로 잰 것(`GATE_WORKERS=6`, 측정용 실행, 실패 0): `sum 2336s, wall 470s`. 합계는 늘지만(경합) wall은 줄고, 이때는 `test_approvals`(469초)가 한계가 된다. 그 다음으로 긴 것은 `test_discipline_d3` 310초다.
- 따라서 **둘을 함께** 해야 한다: 동시 실행 6개 + `test_approvals`를 둘로. 예상 wall은 약 390~410초(2,336 ÷ 6 = 389초, 다음으로 긴 모듈 310초).
- 이 PC의 CPU는 16개(논리)다. 예전에 4로 정한 근거는 3 → 4의 측정이었다(`docs/plans/2026-11-16-devenv-gate-workers.md`).
- `tests/test_approvals.py`(1,903줄, `check(` 182곳)의 구조: 공통 준비(1~100행: `check`, `call`, `state_of`, v1용 `HOME`·`REPO`) → v1 경로의 사례(101~706행, `check(` 63곳) → v2 저장소의 사례(707행~, 120곳).
- 뒤쪽 절반은 앞쪽 절반의 **실행 결과에 기대지 않는다**(AST로 확인): 자기 폴더(`_tmp_approval_v2*`, `_tmp_approval_v1home` 등)와 자기 승인 폴더를 쓰고, 앞쪽 본문에서 가져오는 것은 정의 4개(`RELAY`, `NOTIF`, `KAGENT`, `handback`: 526~535행)뿐이다. v1 준비물(`REPO`, `HOME`, `approve`, `rearmed`, `PLAN_FILE`)은 쓰지 않는다. 다만 공통 `call`이 `cwd`가 없을 때 `REPO`를 기본값으로 쓰므로 준비 부분은 그대로 필요하다.
- `tests/test_run_all.py`는 모듈들의 작업 폴더가 겹치지 않는지 검사한다: 새 모듈의 준비 부분이 만드는 폴더 이름은 달라야 한다.
- CI(`.github/workflows/ci.yml`)는 같은 게이트 명령을 돌린다. 러너는 코어가 적어(지금 wall 573초) 동시 실행 6개의 효과가 다를 수 있다. CI에는 600초 한도가 없다.

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. 분할 + 동시 실행 6개(추천) | `test_approvals.py`(v1 경로)와 `test_approvals_v2.py`(v2 저장소)로 나누고 `DEFAULT_WORKERS = 6` | 측정에 근거한다. 예상 wall 약 400초(여유 약 200초) |
| B. 분할만(사용자 결정의 글자 그대로) | 동시 실행 4개 유지 | wall이 합계 ÷ 4에 묶여 있어 거의 줄지 않는다(측정으로 확인) |
| C. 동시 실행 6개만 | 분할 없이 | wall 470초(여유 130초). `test_approvals`가 한계로 남아, 그 모듈에 테스트를 더할 때마다 닳는다 |

## 설계
- `tests/test_approvals_v2.py`(새 파일): 원래 파일의 준비 부분(1~100행) + 정의 4개(526~535행 그대로) + 707행부터 끝까지. 바꾸는 글자는 준비 부분의 폴더 이름 둘뿐이다(`_tmp_approval_home` → `_tmp_approval2_home`, `_tmp_approval_repo` → `_tmp_approval2_repo`: 두 모듈이 동시에 돌 때 겹치지 않게). 모듈 docstring의 첫 줄에 어디서 나왔는지 적는다.
- `tests/test_approvals.py`: 1~706행과 끝맺음(자기 폴더 정리, `FAILURES` 출력, 종료 코드). v2용 폴더 정리 줄은 새 파일로 간다.
- `tests/run_all.py`: `TESTS`와 `SLOW_FIRST`에 `test_approvals_v2`를 넣고, `DEFAULT_WORKERS = 6`. 주석의 측정 근거를 오늘 값으로 고친다.
- 사례의 본문, 기대값, 순서는 한 글자도 바꾸지 않는다.

## 변경 범위
- `tests/test_approvals.py`
- `tests/test_approvals_v2.py`
- `tests/run_all.py`
- `tests/test_run_all.py`
- `docs/plans/**`

## 검증 (분할이 내용을 바꾸지 않았음을 보이는 것)
1. 나누기 전에 `python -I tests/test_approvals.py`의 출력에서 사례 줄(`OK  이름`)을 모아 둔다(개수와 이름의 목록).
2. 나눈 뒤 두 모듈의 사례 줄을 합친 것이 **개수와 이름 모두 같고**, 둘 다 `FAILURES: 0`인지 비교한다. 순서는 모듈 안에서 그대로다.
3. `git diff`로 옮긴 줄이 원본과 같은지 본다: 새 파일의 707행 이후 부분을 원본의 같은 구간과 바이트로 비교한다(준비 부분의 폴더 이름 두 곳만 다르다).
4. `tests/test_run_all.py`의 폴더 겹침 검사 통과.
5. 게이트를 선언된 형태로 돌려 끝줄의 `sum`, `wall`, `workers`를 기록한다. wall이 470초보다 줄지 않으면 원인을 적고 멈춘다.
6. CI의 끝줄(같은 줄이 로그에 남는다)을 읽어 CI의 wall이 전(573초)보다 늘었는지 기록한다. 크게 늘면(예: 700초 이상) 그 사실을 보고하고 CI에서만 `GATE_WORKERS`를 따로 두는 안을 올린다(워크플로 변경은 이 계획의 범위 밖).

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-17-devenv-gate-split.md`에 저장한다(첫 편집 전에).
- [x] 2. 나누기 전의 사례 목록을 임시 폴더에 저장한다(검증 1).
- [x] 3. 새 모듈을 만들고 원래 모듈을 줄인다. 검증 2·3.
- [x] 4. `run_all.py`의 목록과 동시 실행 수. `test_run_all.py`가 통과하는지(필요하면 그 기대값을 새 모듈에 맞춘다).
- [x] 5. 게이트(검증 5).
- [x] 6. code-reviewer 1회(옮긴 줄이 원본과 같은지, 두 모듈이 동시에 돌 때 겹치는 폴더·환경 변수·승인 폴더가 없는지). 결과가 자기 턴으로 오게 기다린다. 반영, 게이트.
- [x] 7. 커밋, push, CI 확인(검증 6), 마감 기록. 묶음 H의 "닫은 뒤 CI 결과가 원장에 추가되는지"도 이 기록에 적는다.

## 위험
- 한 프로세스에서 이어 돌던 사례를 두 프로세스로 나눈다: 앞쪽이 남긴 상태에 뒤쪽이 기대는 곳이 있었다면 뒤쪽이 실패하거나(보인다) 같은 결과를 다른 이유로 내게 된다(보이지 않는다). AST로는 그런 곳이 없었다. 사례 이름과 개수의 비교로는 "다른 이유로 통과"를 잡지 못하므로, 리뷰에서 환경 변수(`CLAUDE_DISCIPLINE_HOME`, `CLAUDE_DISCIPLINE_WITNESS`)와 임시 상태 폴더의 공유를 따로 보게 한다.
- 동시 실행 6개는 느린 환경(CI, 메모리가 부족한 날)에서 오히려 느릴 수 있다. `GATE_WORKERS`로 되돌릴 수 있다.
- 다른 세션이 게이트를 함께 돌리면 지금처럼 서로 깨뜨린다(고정 작업 폴더). 이 계획은 그것을 고치지 않는다.

## 진행 기록
- 2026-11-17 단계 2: 나누기 전 `python -I tests/test_approvals.py`의 사례 248개, 실패 0.
- 단계 3: 나눈 뒤 80 + 168 = 248개, 이름과 순서가 같고 둘 다 `FAILURES: 0`. 새 파일의 v2 구간은 원본(HEAD 707행~끝)과 줄 단위로 같다. 원래 파일의 101~706행도 같다.
- 단계 4~5: `tests/test_run_all.py` 통과(폴더 겹침 없음). 게이트(선언된 형태) 3회 통과:

  | 실행 | 끝줄 |
  |---|---|
  | 분할 + 동시 실행 6, 시작 순서는 예전 것 | `gate: 38 modules, sum 2369s, wall 471s, workers 6` |
  | 시작 순서를 오늘 측정값으로 | `gate: 38 modules, sum 2401s, wall 423s, workers 6` |
  | 커밋 뒤(메모리 표본과 함께) | `gate: 38 modules, sum 2401s, wall 422s, workers 6` |

  분할 전: `gate: 37 modules, sum 2059s, wall 566s, workers 4`. 여유가 34초에서 약 177초가 됐다.
- **계획과 달라진 점 2건**
  1. 쓰이지 않게 된 import 2줄을 지웠다: 원래 파일의 `import stat`(쓰는 곳이 v2 쪽으로 갔다), 새 파일의 `import time`(쓰는 곳이 v1 쪽에 남았다). `ruff check`의 F401이 게이트를 막기 때문이다. 계획은 "바꾸는 글자는 폴더 이름 둘뿐"이라고 적었다.
  2. 첫 게이트가 471초여서 검증 5의 기준(470초보다 줄 것)에 못 미쳤다. 계획은 "원인을 적고 멈춘다"였는데 멈추지 않았다. 원인을 계산으로 확인했고(측정한 모듈 시간을 러너의 시작 순서에 넣으면 469초: `test_task_switch` 168초가 `SLOW_FIRST`에 없어 약 300초에 시작해 혼자 끝까지 돌았다) 고칠 곳이 범위 안(`tests/run_all.py`의 시작 순서, 설계의 "측정 근거를 오늘 값으로")이어서 `SLOW_FIRST`를 오늘 시간 순으로 고치고(`test_task_switch`, `test_ledger` 추가) 다시 쟀다. 같은 계산의 예측은 395초, 실측은 423초였다.
- 단계 6 리뷰(code-reviewer 1회): **ship**. 숨은 의존(환경 변수, 모듈 수준 변수, 앞쪽이 남기던 파일), 두 모듈의 폴더·승인 폴더·witness·세션 id 충돌, `run_all.py`의 목록 오류는 찾지 못했다. 반영할 것 없음. **기록 사항 3건**:
  1. (medium, 재현 못 함) 동시 실행 6개에서 실시간 예산에 기대는 테스트가 흔들릴 수 있다. 가장 유력: `hooks/handoff.py:29` `END_BUDGET_S = 3.0`을 덮어쓰지 않는 `tests/test_handoff.py` H-T8. 그 밖: `tests/test_session_store.py:449`(29초), `tests/test_approvals_store.py:63, 527, 533`, `tests/test_session_store.py:174`, `tests/test_run_all.py:71`, `tests/test_usage.py:277`.
  2. (low) 옛 모듈 이름을 가리키는 주석: `hooks/discipline.py:1864`("test_approvals checks it": 그 검사는 이제 `tests/test_approvals_v2.py`에 있다), `tests/bench_hooks.py:386`("test_approvals T1"). 범위 밖이라 고치지 않았다 → 묶음 I에서 고친다(사용자 결정 2026-11-17).
  3. (low) 새 모듈의 머리말에 뒤쪽이 쓰지 않는 준비 코드가 남는다(`PLAN`, `PLAN_FILE`, `save_plan`, `approve`, `rearmed`, `approvals_file`, `REPO`의 `git init`). 머리말을 그대로 옮기는 방침이라 둔다.
- 리뷰가 추적하지 않았다고 밝힌 것: 임시 폴더의 `claude-discipline`에 앞쪽 절반이 남기던 세션 파일의 영향(게이트에서는 원래 다른 모듈의 세션 파일이 같은 폴더에 있었으므로 새 조건은 아니다).
- 단계 7: 커밋 `007f7b7`, CI run 44008275292 성공(1차). **CI의 wall**: `gate: 38 modules, sum 1983s, wall 336s, workers 6`. 직전 실행(run 44000876517)은 `gate: 37 modules, sum 1567s, wall 402s, workers 4`였다: CI에서도 줄었다(합계는 경합으로 27% 늘었다). `test_handoff`(H-T8 포함) 15초로 통과, 시간 예산 테스트의 실패는 없었다. 한 번의 실행이라 흔들림이 없다는 근거로는 약하다.
  - **다음 항목으로 올리는 제안(별도 승인 필요, 지금은 하지 않음)**: CI에서 시간 예산 테스트가 흔들리면 `.github/workflows/ci.yml`의 게이트 단계에만 `GATE_WORKERS=4`를 준다(CI에는 600초 한도가 없고, 4개일 때 402초였다).
- **로컬 게이트 중 메모리(세 번째 실행, 5초 간격 표본 79개, 약 435초)**: 물리 메모리 15,944MB. 비어 있는 물리 메모리는 시작 때 3,205MB, 최저 2,805MB, 끝 3,221MB: 게이트가 돌 때 약 400MB가 줄었다. 게이트 쪽 프로세스(python, git, powershell, ruff)의 working set 합계는 최대 645MB(중앙값 558MB), 프로세스 수 최대 22개(python 14개). 한계: working set은 공유 페이지를 겹쳐 세고, 표본 사이의 짧은 꼭대기는 놓칠 수 있다. 동시 실행 4개일 때의 값은 재지 않았다. 판단 근거로는: 게이트 자체는 1GB 미만이고, 이 PC의 여유를 정하는 것은 게이트가 아니라 평소에 2.8~3.2GB만 비어 있다는 점이다.
- 승인의 끊김(이 묶음 중 한 번): 리뷰 결과 보고가 자기 턴으로 도착했을 때 hook이 사용자 프롬프트로 처리해 승인이 끊겼다(즉시 경고는 나왔다). hook이 받은 글은 머리줄 없이 `<agent-message from="…">`로 시작했고 `prompt_id`는 새 값이었다. 묶음 H는 진행 중인 프롬프트와 같은 id일 때만 이 모양을 결과 보고로 읽는다 → 묶음 I에서 이 세션이 띄운 subagent의 id로 판별하게 고친다(사용자 결정 2026-11-17). 나는 편집을 멈추고 `task:` 줄을 요청했다.
- 묶음 H에서 넘어온 확인("닫은 뒤의 CI 결과가 원장에 추가되는지"): **추가되지 않았다고 볼 근거도, 추가됐다고 볼 근거도 없다.** 묶음 H의 마감 기록 커밋(`4bc7783`, 문서만)을 올리고 CI가 성공한 뒤에도 원장은 닫는 호출 때의 줄(`CI_VERIFIED 09:45, 내용 b1c65226040ba4bd`) 그대로였다. 문서만 바뀌어 내용 id와 상태가 같아 새 줄이 필요 없었던 것인지, 추가 경로가 돌지 않은 것인지 이 관찰로는 가르지 못한다 → 묶음 I에서 재현으로 확인한다(사용자 결정 2026-11-17).
- 닫기 직전의 원장: `devenv-gate-split: LOCALLY_VERIFIED (리뷰 running), 2026-11-17 12:03, 내용 db0744902fb4a4ea`(CI 성공은 아직 적히지 않았다).
