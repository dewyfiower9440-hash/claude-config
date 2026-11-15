# 무엇이 쌓이고, 누가 지우는가

이 저장소(`~/.claude`)의 hook과 Claude Code가 남기는 데이터의 위치, 수명, 경고 기준을 적는다.
사실의 근거는 코드와 `tests/_baseline/2026-11-11-p0-02.md`다. 확인하지 못한 것은 **미확인**으로 적었다.
측정값은 2026-11-15 기준이다.

## 쌓이는 것

| 종류 | 위치 | 만드는 쪽 | 지우는 쪽 | 2026-11-15 |
|---|---|---|---|---|
| 대화 기록 | `~/.claude/projects/<폴더별>/` | Claude Code | Claude Code, `cleanupPeriodDays`(기본 30일) 뒤 | 193개, 471MB |
| 파일 이력 | `~/.claude/file-history/` | Claude Code | Claude Code, 같은 설정 | 2,307개, 40MB |
| 세션 상태 `<id>.json`, `<id>.lock` | 임시 폴더의 `claude-discipline/` | `hooks/discipline.py` | `hooks/state_gc.py`(세션 종료 때, 35일 넘게 안 쓰인 것, 한 번에 1초·2,000개까지) | 약 37,000개, 68MB |
| 세션 상태 복구용 사본 `*.recovered-*` | 같은 폴더 | `hooks/discipline.py`(`session_store.keep_copy`) | **아무도 지우지 않는다** | 10개 안팎 |
| 승인 저장소 손상 사본 `*.corrupt-*` | `~/.claude/discipline/` | `hooks/approvals.py` | **아무도 지우지 않는다** | 미확인 |
| 놓친 이벤트 기록 폴더 `<id>.notes/` | 같은 폴더 | `hooks/session_store.py` | 안의 기록은 읽힌 뒤 지워지고, 빈 폴더는 남는다 | 드묾 |
| 종료 기록 | `~/.claude/state/handoff/` | `hooks/handoff.py` | 저장소마다 파일 하나를 덮어쓴다. 14일 지난 것은 읽지 않는다 | 저장소 수만큼 |
| 작업 원장 | `~/.claude/state/ledger/` | `hooks/ledger.py` | **지우지 않는다**(상태가 바뀔 때 약 260바이트 한 줄) | 아직 거의 없음 |
| 사용량 기록 | `~/.claude/state/usage.json` | `hooks/usage.py` | 파일 하나를 덮어쓴다 | 1개 |
| 승인 기록, 관측 기록(`*-observe.jsonl`), 복구 감사 기록 | `~/.claude/discipline/` | `hooks/discipline.py`, `hooks/approvals.py` | **지우지 않는다**. 관측 기록은 크기 상한이 없다 | 미확인(도구로 읽지 못하게 막힌 폴더) |
| 계획 문서, 기준선 측정 | `docs/plans/`, `tests/_baseline/` | 작업 중 작성 | git으로 관리 | — |

- `cleanupPeriodDays`는 `settings.json`에 따로 정하지 않았다(기본값 30일). 이 값을 늘리면 대화 기록과 파일 이력이 더 오래 남는다. 세션 상태의 보존 기간(35일)은 이 값보다 길어야 한다: 대화를 다시 열 수 있는 동안 상태가 먼저 지워지면 "검증 안 된 변경" 기록이 표시 없이 사라진다(`hooks/state_gc.py`의 설명). 30일보다 늘릴 때는 환경 변수 `CLAUDE_STATE_TTL_DAYS`도 함께 늘린다.
- 게이트(`python -I tests/run_all.py`)의 테스트는 전용 임시 폴더에서 돌고 끝나면 지워진다(P0-15). 그 전에 쌓인 세션 상태 약 37,000개는 35일이 지나면서 세션 종료 때마다 조금씩 줄어든다.

## 측정과 경고

- 세션이 끝날 때 `hooks/handoff.py`가 `hooks/usage.py`로 위 폴더들의 **파일 수와 크기 합, 디스크 여유 공간**을 재서 `~/.claude/state/usage.json`에 적는다. 파일 이름·경로·내용은 적지 않는다. 전체 1초를 폴더들이 나눠 쓰고(끝없이 자라는 세션 상태와 승인 저장소 폴더를 먼저 잰다), 제 몫 안에 다 세지 못한 폴더는 그때까지의 값에 `partial` 표시를 한다. `partial`인 값은 실제보다 작으므로, 기준 아래로 보여도 실제로는 넘었을 수 있다. 종료 hook에 시간이 모자라면 건너뛴다.
- 다음 세션 시작 때 `hooks/session_context.py`가 그 기록을 읽어 기준을 넘은 항목을 한 줄씩 알린다. 14일보다 오래된 측정은 쓰지 않는다. 요약(compaction) 뒤에는 다시 알리지 않는다.
- **자동으로 지우는 것은 없다.** 경고는 알림이다.

| 항목 | 기준(초기 값) | 2026-11-15 측정 |
|---|---|---|
| 대화 기록 크기 | 2,000MB | 471MB |
| 파일 이력 크기 | 500MB | 40MB |
| 세션 상태 파일 수 | 100,000개 | 약 37,000개 |
| 승인 저장소 폴더 크기(관측 기록, 승인 기록, 손상 사본 포함) | 50MB | 미확인 |
| 디스크 여유 | 20GB 미만이면 경고 | 272GB |

기준은 근거가 약한 초기 값이다(`hooks/usage.py`의 `LIMITS`, `FREE_GB_LIMIT`). 경고가 잦거나 실제로 불편해진 크기와 맞지 않으면 그 값을 고치고, 바꾼 이유와 측정값을 이 문서에 적는다.

## 경고가 나오면

1. 무엇이 큰지 본다: `python -I -c "import sys; sys.path.insert(0,'hooks'); import usage; print(usage.read())"` (저장소 루트에서).
2. 대화 기록·파일 이력이 크면: `cleanupPeriodDays`를 줄이는 것을 검토한다(설정 변경은 사용자가 결정한다). 직접 지울 때는 지금 쓰는 세션의 폴더를 피한다.
3. 세션 상태 파일이 많으면: 35일 정리가 따라가는지 본다(세션 종료 한 번에 1초 안에서 2,000개까지 지운다. 파일이 수만 개인 폴더에서는 목록을 읽는 데 시간이 들어 실제로는 수백 개 수준이다). 게이트 밖에서 테스트 파일을 직접 여러 번 돌리면 실제 임시 폴더에 다시 쌓인다.
4. 관측 기록이 크면: 필요한 집계를 낸 뒤(`python -I hooks/authz_report.py`, 사용자가 직접 실행) 사용자가 파일을 정리한다. 이 폴더는 도구가 읽지 못하게 막혀 있다.
5. 원장, 복구용 사본, 빈 `.notes` 폴더는 작지만 지우는 장치가 없다. 정리 방법은 아직 정하지 않았다(별도 제안).
