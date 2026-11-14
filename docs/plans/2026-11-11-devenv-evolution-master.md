---
status: in-progress
task: devenv-master
created: 2026-11-11
approved: 2026-11-11
---

# 개발환경 고도화 4단계 마스터플랜

이 문서는 로드맵 전체를 관리합니다. 각 단계는 시작할 때 별도 계획 파일(`2026-..-devenv-p0-safety.md`, `task: devenv-p0` 등)로 만들고 단계마다 따로 승인을 받습니다. 이 마스터 승인은 P0-01·P0-02(읽기·측정)에만 적용됩니다.

## 결정 기록 (2026-11-11)
- 마스터플랜 승인.
- **P0-06 = B. 승인 없는 코드 수정 0건** (2026-11-11 정정, 이전 결정 A는 폐기).
  - 승인된 계획 하나(task_id + git root + 계획 해시 + `## 변경 범위` 파일 범위)가 있으면, 그 범위 안에서는 개발·테스트·수정·재검증을 자동으로 반복합니다. 파일마다 다시 승인을 받지 않습니다.
  - 범위 밖 경로, 다른 task_id, 다른 프로젝트, data·backup 경로는 승인 범위가 따로 있어야 허용됩니다. 해당 승인이 없으면 거부합니다.
  - CLAUDE.md §2의 "1-2파일 작은 변경은 바로 진행" 규칙은 "작은 변경도 경량 승인 계획(ExitPlanMode)을 거친다"로 바꿉니다. 이 문구 수정도 P0-06의 승인 대상입니다.
- **임시 조치**: quant-research의 stale 계획 3개(phase0-1, phase1-completion, step9)는 `status: blocked` + `blocked_reason`으로 표시합니다(done·cancelled 거짓 표시 금지). **CLAUDE.md, discipline, session_context, 테스트에서 blocked 호환성을 검증하기 전에는 커밋하지 않습니다.** 요구사항은 두 가지입니다. blocked 계획은 승인 권한을 부여하지 않아야 하고, 세션 복원 때는 미완료 항목으로 계속 표시되어야 합니다.
- **이 임시 조치는 보안 결함을 해결하지 않습니다.** 승인 후보가 phase4 하나로 줄었을 뿐이고, 결함 자체는 아래 P0 근본 해결 목록으로 다룹니다.

## 현황 재기준 (2026-11-13, 근거: P0-04b 3회 fix first 이후 근본 원인 분석)
- 실제 진행 상태 (아래 체크박스 반영):
  - P0-01~03: LOCALLY_VERIFIED / CI_PENDING (bc9a326, ceaed89, e4363f5)
  - P0-04: v2 구현 완료, 휴면 상태 (e8091d6~4f40f56). 운영 미활성화
  - P0-04b: D1(relay 집계 보존)·D2(TEMP 경로 분류) 구현 완료. D3는 fix first(F1~F3 미해결)이며 `devenv-p0` 브랜치 WIP 8b15d53에 보존
  - P0-04b 마감(2026-11-13 갱신): D3a·D3b·D3c 구현, 리뷰 SHIP 뒤 low 지적 6건 반영(3bd0694와 그 다음 커밋). LOCALLY_VERIFIED / CI_PENDING, push 전. 남은 위험은 `docs/plans/2026-11-12-devenv-p0-04b.md`의 위험·진행 기록 참고
  - F4a 완료(2026-11-14): 세션 상태에 주체(main 또는 `<agent_type>:<agent_id>`)를 기록하고 사유·감사 줄에 표시한다. 판정은 바꾸지 않았다. 리뷰 SHIP 뒤 low 지적 반영. LOCALLY_VERIFIED / CI_PENDING, push 전. 기록은 `docs/plans/2026-11-14-devenv-f4a.md`
  - F4b 완료(2026-11-14): SubagentStart/SubagentStop을 settings.json에 등록했다. hook은 subagent의 시작·종료를 세션 상태에 기록하고, 시작할 때 추적 규칙을 안내하며, 사유 문구에 종료 여부를 표시한다. 판정은 바꾸지 않았다. 리뷰 FIX FIRST(medium 1건) 반영. LOCALLY_VERIFIED / CI_PENDING, push 전. 리뷰 결과와 실사용 확인은 `docs/plans/2026-11-14-devenv-f4b.md`의 진행 기록 참고
  - 게이트 분할(2026-11-14): `test_discipline`의 D3 이후 구간을 `test_discipline_d3`로 옮겼다(check 194개 = 107 + 87, 내용 변경 없음). push 뒤 CI run 43569565401 실패(콘솔 인코딩 2건, 테스트의 경로 비교 1건)를 고쳤고 run 43574299831(cdc3282)에서 통과했다. P0-01~F4b와 이 분할까지 CI_VERIFIED. 기록은 `docs/plans/2026-11-14-devenv-gate-split.md`
  - v2 승인 저장소 운영 활성화(2026-11-14): 사용자가 터미널에서 `python -I hooks/approvals.py activate`를 직접 실행했다(출력 `완료: gen=1`). 이후 승인은 v2 경로로만 발급·검증된다. R1 완료(2026-11-14): 이 세션이 띄운 subagent의 결과 보고 뒤에는 v2 승인 포인터를 유지한다(CI run 43590899088). 기록은 `docs/plans/2026-11-14-devenv-r1.md`
  - P0-05 완료(2026-11-15): 계획 폴더 예외를 승인 저장소 안으로 한정하고, git 밖 경로는 `## 데이터 변경 범위` 블록으로만 허용한다. CI run 43616841549. 기록은 `docs/plans/2026-11-14-devenv-p0-05.md`
  - P0-06 구현 완료, 강제 대기(2026-11-15): 승인 없는 수정 0건 정책을 `CLAUDE_AUTHZ_MODE`로 넣었다. 기본값은 관측(warn)이라 5파일 허용은 그대로이고 C3은 아직 닫히지 않았다. 강제 전환은 관측 1주 뒤 별도 작업이다. CI run 43620094440. 기록은 `docs/plans/2026-11-15-devenv-p0-06.md`
  - P0-07 완료(2026-11-15): 셸 명령에서 읽어 낼 수 있는 쓰기 대상을 실행 전에 판정한다(최선 노력, 한계는 `hooks/shellwrites.py` 설명). 기본 모드에서는 관측만 한다. CI run 43624307591. 기록은 `docs/plans/2026-11-15-devenv-p0-07.md`
  - P0-08 일부 완료(2026-11-15): 데이터·백업 폴더를 settings의 편집 도구 deny에 넣었다. 백업 폴더 ACL은 하지 않았다(같은 계정으로 도는 백업을 멈출 수 있고, 전용 계정은 Quant 쪽 결정 사항). CI run 43626075307. 기록은 `docs/plans/2026-11-15-devenv-p0-08.md`
  - 게이트 러너 병렬화: 3b276ca
- **잘못된 요구사항 정정**
  - D3 "어떤 경로로도 미검증 변경이 사라지지 않음"은 hook 경계 밖의 조건이라 달성할 수 없었다. 다음 내용으로 재정의한다.
    - 기준선이 있는 저장소는 현재 내용의 증거 없이 VERIFIED가 되지 않는다.
    - 사라진 저장소는 증명이 없으면 UNKNOWN으로 둔다.
    - UNKNOWN은 명시적 사용자 확인으로만 해제한다.
    - 셸 내부 쓰기 통제는 P0-07(최선 노력)과 OS 경계 몫이다.
  - R1은 v2 활성화 없이는 안전하게 풀 수 없다. P0-04b에서 빼서 v2 활성화 이후로 옮긴다(순환 해소).
  - 세션 상태 원자화는 승인된 어느 계획에도 없었다("P0-04 단계 5 누락"이라는 이전 보고는 오류). **신규 항목 S로 정의한다.**
- **재정렬 순서**: S(`docs/plans/2026-11-13-devenv-p0-s.md`) → P0-04b 마감(D3a·D3b·D3c) → F4a(agent_id 소유권) → F4b(SubagentStart/Stop 등록) → v2 운영 활성화 → R1 → P0-05 → P0-06 → P0-07 → P0-08 → P0-09~13 → P0-14 → P1 → P3 → P2
- **P4는 이 마스터플랜에 정의되어 있지 않다.** 범위는 사용자 정의가 필요하다.
- 별도 승인이 필요한 항목:
  - D3 계약을 반영한 P0-04b 개정
  - D3c `ack-missing` 신규 기능
  - F4b settings 변경
  - v2 활성화
  - R1 규칙 변경
  - push/CI
  - P0-06 CLAUDE.md 개정
  - P0-08 ACL
  - P0-11 메모리 이동
  - P3 evaluator와 구독 사용량
  - P4 정의

## P0 근본 해결 필수 목록 (임시 조치와 무관하게 전부 해결)
1. 이전 task_id의 승인 재사용 → P0-03, P0-04
2. 다른 프로젝트의 권한 상속 → P0-03(git_root 바인딩), P0-10
3. 승인 범위 밖 경로 허용(타 저장소 `/docs/plans/` 예외 포함) → P0-05
4. 승인 없는 파일 수정(Edit 5개 허용, Bash 사전 통제 없음) → P0-06, P0-07
5. 코드 승인과 데이터 변경 권한 혼합 → P0-05(경로 클래스), P0-08

## 치명적 결함 (우선순위)
1. **C1** 오래된 승인 재무장: `rearm_approval`이 프롬프트의 작업을 보지 않고, 진행 중인 계획 중 파일명 순으로 처음 유효한 것을 고릅니다(`discipline.py:481-528`). 여기에 더해 다른 저장소의 `/docs/plans/` 경로도 범위 안으로 판정합니다(`:553-554`, N1).
2. **C2** 원본 데이터 무방비: qr-data와 qr-backup이 `Edit(//c/dev/**)` 허용 범위에 들어가고(`settings.json:172`), git 밖이라 변경 감지도 되지 않습니다. 코드와 데이터 범위가 한 승인으로 묶여 있습니다(N2).
3. **C3** 승인 없이 턴당 5파일까지 수정할 수 있습니다(`:45`).
4. **C4** 컨텍스트 오주입·누락: 저장소 밖에서 시작하면 주입이 0이고, 메모리가 섞여 있으며, in-progress 계획이 여러 개여도 1개만 표시합니다.

## 목표 아키텍처 요약
- 순수 모듈: `resolve_project`, `bind_task`, `authorize(path, op, PathClass, binding, approval)`, `ledger.transition`, `handoff.build`. I/O는 훅 진입점에서만 합니다.
- 식별과 승인을 분리합니다. 재무장 조건은 같은 git root, 프롬프트(또는 이번 세션의 ExitPlanMode)에 task_id가 명시될 것, 계획 해시 일치, in-progress, 미만료, 미취소입니다. 하나라도 어긋나면 권한은 0입니다.
- handoff와 ledger는 상태만 복원합니다. 권한은 다시 주지 않습니다.
- 평가는 별도 `evaluator` Windows 계정이 맡고, 개발 계정은 읽기 거부 ACL로 막습니다.
- Quant 저장소의 코드와 상태는 이 로드맵에서 수정하지 않습니다. 단, 위 임시 조치는 예외입니다.

## 공통 사항
- 롤백: claude-config 커밋을 revert하고, 바꾸기 전에 `backups/settings-<date>.json`을 저장합니다. ACL은 `icacls /restore`로 되돌립니다.
- 오탐 완화: `CLAUDE_AUTHZ_MODE=warn` 관측 모드로 1주 운영합니다.
- 수치(7일, 14일, 8KB, +150ms 등)는 모두 초기 prior입니다. 실측한 뒤 재조정합니다.
- subagent는 동시에 2개 이하로 둡니다(RAM 16GB, 상시 수집기).

## 단계

### P0 안전·세션 (단계 계획 `devenv-p0`에서 상세화)
- [x] 1. P0-01 기준선: 훅별 지연(p50/p95, 20회), 세션 A–G 주입 결과, 공격 코퍼스를 현재 코드에서 실행합니다(`tests/bench_hooks.py`). 설정 태그 `devenv-baseline-2026-11-11`은 만들기 전에 다시 확인합니다.
- [x] 2. P0-02 공식 기능 확인: SessionEnd 페이로드, resume·fork의 session_id와 source, `--add-dir` 전달 여부, auto-memory 키, Windows Bash sandbox, `cleanupPeriodDays`, PreToolUse additionalContext. 항목마다 "공식 / 커스텀 필요 / UNKNOWN"으로 판정하고 프로브 결과를 붙입니다.
- [ ] 3. P0 단계 계획 작성 및 승인 (P0-03~P0-14)
- [x] 4. P0-03 `bind_task`: 재무장 수정. 재현 스크립트를 실패 테스트로 먼저 추가합니다.
- [ ] 5. P0-04 승인 수명: done·cancelled·**blocked**, 미체크 0개, 경로 변경 시 무효화합니다. 상태 목록을 `draft|in-progress|blocked|done`으로 확장합니다(CLAUDE.md §2, quant CLAUDE.md, `/plan`, scaffold 템플릿). `plan_hash`는 frontmatter를 제외하므로 blocked에서 in-progress로 되돌리면 옛 기록이 다시 유효해집니다. 그래서 blocked가 되면 해당 기록을 revoked에 넣습니다. `plan_path`와 `git_root`를 서명하고 revoked 목록을 둡니다. 만료는 마지막 사용 후 7일과 절대 30일을 함께 적용합니다.
- [x] 6. P0-05 `docs/plans` 예외를 승인 base 안으로 한정하고 경로 클래스를 도입합니다. data는 `## 데이터 변경 범위` 블록이 있어야 허용합니다.
- [x] 7. P0-06 무승인 수정 정책 = B(승인 없는 코드 수정 0건, 승인된 task 범위 안에서는 반복 자유). CLAUDE.md §2를 수정합니다(승인 필요).
- [x] 8. P0-07 Bash와 PowerShell의 쓰기 대상을 사전에 authorize합니다(`shellparse` 재사용). 사전 예측이 완전하지 않다는 점을 문서화합니다.
- [x] 9. P0-08 데이터 보호: qr-data와 qr-backup에 deny를 추가하고 qr-backup에 쓰기 거부 ACL을 겁니다. 수집기 전용 계정은 Quant 쪽에 제안만 합니다. **설정·ACL은 별도 승인을 받습니다.**
- [ ] 10. P0-09 resolver: 저장소 밖에서 시작하면 프로젝트 목록을 보여주고, 저장소 안이면 in-progress 계획 전부, **blocked 계획(blocked_reason과 미체크 수)**, 불일치 경고를 함께 보여줍니다. blocked 계획은 보여주기만 하고 권한은 주지 않습니다.
- [ ] 11. P0-10 세션 중 다른 git root로 넘어가면 그 프로젝트의 컨텍스트를 한 번 주입하고 승인은 0으로 둡니다.
- [ ] 12. P0-11 메모리를 전역과 프로젝트로 분리하고 `verified_at`과 `source`를 추가합니다. **메모리 이동은 별도 승인을 받습니다.**
- [ ] 13. P0-12 SessionEnd handoff를 추가합니다. 승인 필드는 기록하지 않습니다.
- [ ] 14. P0-13 discipline 상태 파일 TTL 정리(현재 2,973개)
- [ ] 15. P0-14 전체 gate, 공격·정상 코퍼스, 지연 재측정, revert 리허설, CI green. **push는 승인을 받습니다.**

### P1 신뢰성 (단계 계획 `devenv-p1`)
- [ ] 16. P1 단계 계획 작성 및 승인
- [ ] 17. P1-01 서명된 task ledger 상태기계(§1.6 상태명과 통합)
- [ ] 18. P1-02 내용 digest에 묶인 리뷰. 오탐을 관측한 뒤 게이트 기본값을 켭니다.
- [ ] 19. P1-03 테스트 누락 탐지
- [ ] 20. P1-04 출력 크기 계측과 test-runner 위임 권고
- [ ] 21. P1-05 CI 실패 원인 분류
- [ ] 22. P1-06 재시작 복구(ledger, git, 계획 대조)
- [ ] 23. P1-07 `projects/`와 file-history 보존 정책, 디스크 경고
- [ ] 24. P1-09 handoff와 ledger redaction
- (P1-08 rest.log 회전은 Quant 저장소 계획으로 넘깁니다. 이 로드맵에서는 증가율만 감시합니다.)

### P3 평가 기반 (P2 효과 판정에 먼저 필요)
- [ ] 25. P3 단계 계획 작성 및 승인. evaluator 계정과 구독 사용량은 별도 승인을 받습니다.
- [ ] 26. P3-01 evaluator 계정, 과제 저장소, 홀드아웃 30%
- [ ] 27. P3-02 headless 실행기와 숨은 테스트 채점
- [ ] 28. P3-03 지표 구현
- [ ] 29. P3-04 사전 등록 프로토콜(Wilson 95% 구간)
- [ ] 30. P3-05 개선 루프(제안 큐 → 평가 → 채택·롤백)

### P2 적응 (P3-01~03 이후에만 효과를 판정)
- [ ] 31. P2 단계 계획 작성 및 승인
- [ ] 32. P2-01 위험 점수, P2-03 대안 비교 트리거
- [ ] 33. P2-02 모델 라우팅 실험(기본값은 최고 성능 모델로 유지)
- [ ] 34. P2-04 실패 재발 감지와 Gotchas 후보 제안(자동 반영하지 않음)
- [ ] 35. P2-05 메모리 검색
- [ ] 36. P2-06 `/implement`와 ledger 연동(승인 게이트는 유지)

## 단계별 완료 게이트
| 단계 | 게이트 |
|---|---|
| P0 | `python -I tests/run_all.py` 통과, 공격 코퍼스 100% 차단, 정상 코퍼스 100% 통과, 세션 A–G 판정표, 훅 p95 지연 증가 ≤ +150ms(prior), revert 리허설, CI green(run id) |
| P1 | 거짓 완료 시나리오 전부 BLOCKED, 재시작 복원 테스트, CI 분류 테스트, gate 통과 |
| P2 | P3 실험에서 비열등이고 효과 구간을 보고합니다. 효과가 없으면 채택하지 않습니다. |
| P3 | 재현성, 개발·평가 격리 증명, 사전 등록 무결성 |

## 채택·보류·롤백 기준
- 채택: 핵심 지표가 비열등(구간 하한 ≥ −5%p)이고 목표 지표 개선 구간이 0을 넘으며, 보안 지표가 악화되지 않을 때
- 보류: 구간이 0을 포함할 때
- 롤백: 보안 지표 악화, 회귀, 지연 게이트 초과

## 하지 않을 것
- 메모리나 Gotchas를 전역 규칙에 자동 반영하지 않습니다.
- 승인을 자동 연장하거나 세션 복원 때 권한을 다시 주지 않습니다.
- 병렬 에이전트를 무제한으로 쓰지 않습니다.
- 평가 과제를 개발 계정에서 읽을 수 있게 두지 않습니다.
- 실행 중인 수집기를 갑자기 재시작하지 않습니다.
- 새 MCP, 새 리뷰 에이전트, 별도 메모리 DB, 범용 플러그인 시스템을 추가하지 않습니다.

## 기준선 (개선 전)
- 공격 코퍼스에서 확인된 실패 3건: 재무장, 다른 저장소 계획 파일, 데이터 경로
- 무승인 수정 허용치 5파일/턴, 세션 A·B 주입 0, 표시되는 계획 4개 중 1개
- 훅 지연 **UNKNOWN**(P0-01에서 측정), 완료율·거짓 완료율 **UNKNOWN**(P3에서 측정)
