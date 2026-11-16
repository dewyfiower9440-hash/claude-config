---
status: in-progress
task: devenv-gate-isolation
---

# devenv: 두 테스트 모듈이 같은 작업 폴더를 쓴다 — 동시 실행에서 서로를 지운다

- 앞 계획: `docs/plans/2026-11-16-devenv-gate-workers.md`(단계 1~3까지 했고 4단계의 게이트가 실패했다). 이 계획은 그 실패의 원인을 고치고 앞 계획의 남은 단계(게이트, 리뷰, 커밋, CI)를 이어받는다.
- 저장 위치: `docs/plans/2026-11-16-devenv-gate-isolation.md`
- 승인 근거: 사용자 지시 2026-11-16 "계획한순서 1번부터 순차적으로 진행해". 고칠 파일(`tests/test_task_switch.py`)이 앞 계획의 변경 범위 밖이라 새로 승인받는다.

## Context
게이트의 동시 실행 수를 3에서 4로 올리고 긴 모듈을 먼저 돌리게 하자 게이트는 435초에 끝났다(전에는 619초, 718초). 시간 문제는 풀렸다. 그런데 모듈 두 개가 실패했다. 테스트를 빼거나 순서를 되돌려 피하지 않고 원인을 찾았다.

## 실패 증거 (2026-11-16, `python -I tests/run_all.py`, exit 1)
- `gate: 37 modules, sum 1719s, wall 435s, workers 4`, `GATE FAILED`.
- `test_project_switch`: 3건 실패(X-T5 "the next user prompt there is announced", X-T6 "moving on: announced…", X-T9 "…one output carrying both"). 모두 "안내가 나와야 하는데 안 나옴".
- `test_task_switch`: `FileNotFoundError: ...\\tests\\_tmp_switch\\tmp\\claude-discipline\\<세션>.json`(자기 세션의 상태 파일이 없다).
- 두 모듈을 하나씩 따로 실행하면 둘 다 `FAILURES: 0`이다.

## 원인 (확인함. 분류: 테스트 fixture 결함 + 동시 실행 경쟁)
- `tests/test_project_switch.py:22`와 `tests/test_task_switch.py:23`이 **같은 폴더** `ROOT = HERE / "_tmp_switch"`를 쓴다. 두 모듈 모두 시작할 때와 끝날 때 그 폴더를 통째로 지운다.
- 두 모듈이 동시에 돌면 한쪽이 다른 쪽의 세션 상태 파일과 fixture 저장소를 지운다: 상태 파일이 없어지고(FileNotFoundError), hook이 저장소를 "처음 보는 것"으로 판단할 근거가 사라져 안내가 나오지 않는다.
- 이 결함은 `test_task_switch.py`가 생긴 2026-11-15(P1-01b)부터 있었다. 동시 실행 수 3과 그때의 실행 순서에서는 두 모듈이 겹치지 않았을 뿐이다. 순서와 수를 바꾸자 겹쳤다.
- 다른 모듈은 폴더가 겹치지 않는다(모든 `HERE / "_tmp…"`와 `C:/dev/_…` 이름을 세어 확인: 두 번 나오는 것은 `_tmp_switch` 하나뿐).
- 부하·시간 제한 탓이 아니다(그 가설은 단독 실행 통과만으로는 구분되지 않아 폴더 이름을 직접 대조해 확정했다).

## 설계 (최소 변경)
- `tests/test_task_switch.py`: `ROOT = HERE / "_tmp_task_switch"`로 바꾼다(한 줄. 나중에 생긴 쪽을 고친다). `.gitignore`는 `/tests/_tmp*/`로 이미 덮는다.
- 재발 방지 `tests/test_run_all.py`: 테스트 모듈들의 작업 폴더 이름(`HERE / "_tmp…"`, `Path("C:/dev/_…")`)을 소스에서 모아 **두 모듈이 같은 이름을 쓰면 실패**하는 검사를 더한다. 이름을 못 찾으면(패턴이 낡으면) 그것도 실패하게 한다.
- 앞 계획의 변경(동시 실행 수 4, 긴 모듈 먼저, 끝의 시간 줄)은 그대로 둔다.

## 변경 범위
- `tests/test_task_switch.py`
- `tests/test_run_all.py`
- `tests/run_all.py`
- `docs/plans/**`

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-gate-isolation.md`에 저장한다(첫 편집 전에).
- [x] 2. `tests/test_run_all.py`에 "작업 폴더가 겹치지 않는다" 검사를 더하고 실패를 확인한다(`_tmp_switch`가 두 번).
- [x] 3. `tests/test_task_switch.py`의 폴더 이름을 바꾼다. 검사 통과.
- [x] 4. 재현 확인: 두 모듈을 **동시에** 실행해 둘 다 통과하는지 본다(고치기 전에는 같은 방법으로 실패가 나는지도 한 번 본다).
- [x] 5. 게이트를 포그라운드에서 실행한다(600초 안, 전체 통과). 이 실행이 P4-02의 미커밋 변경과 게이트 변경을 함께 검증한다.
- [x] 6. code-reviewer 1회(게이트 변경과 이 수정), 반영, 게이트.
- [ ] 7. 커밋 세 묶음(게이트 동시 실행 수 / 테스트 격리 수정 / P4-02), push 전 빈 폴더에서 import 정렬 확인, push, CI 확인(run id와 CI의 게이트 시간), 세 계획의 마감 기록.

## 검증
- 게이트 통과(exit 0)와 끝줄의 `wall`이 600초보다 충분히 작다. CI 성공.
- 4단계의 동시 실행 재현.

## 위험
- 폴더 이름 검사는 소스의 글자를 읽는다. 다른 방식으로 폴더를 만드는 모듈(`tempfile.mkdtemp`)은 겹칠 일이 없어 대상이 아니다.
- 같은 종류의 숨은 경쟁이 더 있을 수 있다(폴더 말고 다른 공유 자원). 4개 동시 실행의 게이트를 이번에 두 번(5·6단계) 돌려 본다. CI는 러너가 달라 한 번 더 본다.

## 진행 기록
- 2026-11-16 단계 1~4: 고치기 전에 두 모듈을 동시에 실행해 실패를 재현했다(`test_task_switch`가 같은 FileNotFoundError). `tests/test_run_all.py`의 새 검사가 `{'_tmp_switch': ['test_project_switch.py', 'test_task_switch.py']}`로 실패하는 것을 확인한 뒤 `tests/test_task_switch.py`의 폴더를 `_tmp_task_switch`로 바꿨다. 동시 실행 3회 모두 두 모듈 통과.
- 단계 5: 게이트 포그라운드 **통과**, `gate: 37 modules, sum 1951s, wall 489s, workers 4`. 동시 실행이 4가 되자 모듈 하나하나는 느려졌다(합 1,719~1,951초). 제한 600초까지 여유는 약 110초다.
- 단계 6: code-reviewer 1회, 판정 FIX FIRST(게이트 동작에는 결함 없음: 빠진 모듈 없음, 판정·종료 코드·출력 순서 그대로, 그 밖에 두 모듈이 공유하는 자원 없음을 확인받았다). 반영:
  - **폴더 겹침 검사의 범위가 좁았다**: `HERE / "_tmp…"`와 `Path("C:/dev/_…")` 두 표기만 봐서 실제 작업 폴더 이름의 3분의 1쯤을 놓쳤다(`Path(__file__).resolve().parent / "_tmp…"`, `TROOT / "_tmp_approval_…"`, `"C:/dev/_disc_test_project"` 같은 문자열). 모든 문자열 리터럴에서 이름을 찾게 넓혔다(67개 이름, 겹침 0). 이 테스트 파일 자신은 다른 모듈의 폴더 이름을 자료로 적으므로 뺀다.
  - 앞 계획에서 넣은 "기본값이 4", "긴 모듈 7개의 목록" 검사는 상수를 상수와 비교하는 것이라 뺐다(재조정할 때마다 깨지기만 한다). 대신 "기본이 병렬(2 이상)", "먼저 시작하는 모듈은 모두 실제 모듈이고 한 번씩만"을 본다. 앞 계획 본문의 "기본값이 4라는 검사"와 달라진 점이다.
  - 실패한 실행에서도 끝줄이 시간 줄인지 확인하는 검사. 주석에 "wall에는 앞의 ruff 두 단계가 들어 있지 않다".
  - 반영분에 대한 추가 리뷰는 없다.
- 리뷰가 알려 준, 동시 실행 4에서 여유가 가장 적은 시간 값(나중에 간헐 실패가 나면 먼저 볼 곳. 지금 실패한 적은 없다): `hooks/handoff.py`의 종료 처리 예산(정리는 1.5초, 사용량 측정은 2.0초 안에 끝나야 실행됨)에 기대는 `test_state_gc`의 G-T11/12와 `test_usage`의 U-T8.
- 반영하지 않은 것(별도 제안): `tests/test_ci_status.py`만 `CLAUDE_DISCIPLINE_HOME`을 따로 두지 않고 hook을 실행한다(리뷰어의 지적, 실제로 실제 승인 폴더에 쓰는지는 미확인). 격리해야 한다.
- 근본 원인으로 남은 것: 모듈 합계가 계속 는다(오늘 1,560 → 1,951초). 여유 110초는 모듈 서너 개면 다시 닳는다. hook 한 번의 실행 비용(프로세스 시작 약 300ms)을 줄이는 것이 본 해결이다(P4-11).
