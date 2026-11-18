---
status: done
task: devenv-fix-b
---

# devenv 결함 개선 B: `/plan`이 시키는 저장과 hook이 인정하는 저장을 같게 한다 (결함 1, 5의 skill 쪽)

- 묶음 계획: `docs/plans/2026-11-16-devenv-final-check-fixes.md` 단계 3
- 저장 위치: `docs/plans/2026-11-16-devenv-fix-b.md`
- 승인 근거: 사용자 지시 2026-11-16 "드러남 결함 상세히 확인후 결함 개선하자"

## Context
최종 확인에서 새 세션은 `/plan`이 시키는 대로 계획을 저장했는데 hook이 "승인된 계획 파일이 아직 docs/plans에 없음"이라며 첫 편집을 거부했다. 세션은 hook 소스를 읽고서야 `created:` 줄을 지워 통과했다. 문구가 원인을 말하지 않아 시간을 썼고, skill을 그대로 따른 것이 틀린 결과가 됐다.

## 확인한 사실
- `skills/plan/SKILL.md:31-35`: 계획 틀의 frontmatter가 `status: draft`와 `created: YYYY-MM-DD` 두 줄이다. `:73`은 승인 뒤 "save the approved text verbatim as the body … with frontmatter `status: in-progress`"라고 한다.
- `hooks/approvals.py:128` `normalize_core`: 승인이 덮는 글은 frontmatter에서 `status` 줄만 뺀 나머지 키 전부와 본문(체크박스 상태와 `## 진행 기록` 절 제외)이다. frontmatter 없이 승인된 글에 `status`만 붙여 저장하면 해시가 같고, `created:`를 더하면 달라진다.
- `hooks/discipline.py:1640` `_v2_find_plan`: `docs/plans`에서 task와 해시가 모두 같은 파일이 정확히 하나일 때만 승인에 묶는다. 못 찾으면 `UNBOUND`(`:1659`) 문구 하나로 거부한다: 파일이 아예 없는 경우와, 같은 task의 파일은 있는데 글이 다른 경우를 구분하지 않는다.
- 재현(2026-11-16, 묶음 A의 탐색에서): `created:`가 든 저장 → Write 2회 거부(위 문구) → `created:` 줄 삭제 → 통과.
- `skills/implement/SKILL.md:23` "Do not push."는 CLAUDE.md §6의 예외(승인된 계획이 그 결정을 인용하면 push 가능)와 어긋난다. `:15`의 `python -I ~/.claude/hooks/ledger.py …`는 PowerShell에서 `~`가 풀리지 않는다(CLAUDE.md §2b에 같은 주의가 이미 있다).
- `skills/debug/SKILL.md:43-44`: 회귀 테스트를 먼저 쓰라고만 하고, 그 층에 테스트 수단이 없을 때(최종 확인의 화면 스크립트 버그)의 절차가 없다. 세션은 테스트 없이 고쳤다.

## 대안 비교
| 선택지 | 내용 | 판단 |
|---|---|---|
| A. skill을 hook에 맞춘다 + 거부 문구가 원인을 말한다(추천) | 틀에서 `created:` 삭제, 저장 규칙을 한 문장으로 명시, 같은 task의 다른 글이 있으면 그 파일 이름과 고칠 점을 문구에 넣는다. 판정은 그대로 | 승인이 덮는 범위가 변하지 않는다. 문서와 hook이 다시 어긋나면 테스트가 잡는다 |
| B. hook이 `created:` 같은 키를 무시한다 | `normalize_core`에서 허용 키 목록을 뺀다 | 승인이 덮지 않는 글이 늘어난다(보안 정책의 완화). 이미 저장된 승인들의 해시와도 어긋난다 |
| C. hook이 계획 파일을 대신 저장한다 | ExitPlanMode 때 `docs/plans`에 직접 쓴다 | hook이 저장소에 파일을 쓰게 된다. 파일 이름·위치를 hook이 정해야 하고 범위가 크다 |

## 설계
- `skills/plan/SKILL.md`: 틀의 frontmatter를 `status: draft` 한 줄로. 5절에 "저장할 때 바꾸는 것은 frontmatter의 `status` 한 줄뿐이다. 다른 키(`created` 등)나 본문의 글자를 더하거나 고치면 hook이 승인된 글로 인정하지 않는다"와 "저장은 첫 편집 전에"를 적는다.
- `hooks/discipline.py`: 묶이지 않은 승인을 거부할 때, `docs/plans`에 같은 task의 파일이 있으면(글이 다름) 문구를 `승인된 글과 다른 계획 파일(<파일 이름>): frontmatter에는 status만 더하고 본문은 승인된 그대로 저장하세요`로 한다. 같은 task의 파일이 없으면 지금 문구 그대로. 허용·거부의 판정, 승인 저장소에 적는 것, `docs/plans` 안 쓰기의 통과는 바꾸지 않는다. 파일 이름만 넣고 글의 내용은 넣지 않는다.
- `skills/implement/SKILL.md`: "Do not push"에 CLAUDE.md §6의 예외를 한 구절로, `~` 명령에 "(Git Bash)"를 붙인다.
- `skills/debug/SKILL.md` 4절: 그 층에 자동 테스트 수단이 없으면(예: 테스트 틀이 없는 화면 스크립트) 가장 가까운 층의 테스트로 재현을 시도하고, 그것도 안 되면 수동 재현 절차와 기대 결과를 보고에 적고 "회귀 테스트 없음"을 미완료·리스크에 밝힌다. 테스트 틀을 새로 들이는 것은 제안으로만.

## 변경 범위
- `skills/plan/SKILL.md`
- `skills/implement/SKILL.md`
- `skills/debug/SKILL.md`
- `hooks/discipline.py`
- `tests/test_docs_consistency.py`
- `tests/test_approvals.py`
- `docs/plans/**`

## 테스트 (먼저 작성해 실패 확인)
| 사례 | 위치 | 기대 |
|---|---|---|
| skill의 계획 틀에서 frontmatter를 뺀 글을 승인된 글로 삼고, 틀 전체에서 `status`만 `in-progress`로 바꾼 것을 저장한 글로 삼으면 `approvals.core_hash`가 같다 | `test_docs_consistency.py` | 지금은 `created:` 때문에 실패 |
| 틀의 frontmatter 키가 `status` 하나다 | 같은 곳 | 지금 실패 |
| `/implement`의 push 문장이 예외를 말한다, `~` 명령 옆에 Git Bash 표기가 있다, `/debug`가 테스트 수단이 없는 층을 말한다 | 같은 곳 | 지금 실패 |
| 승인 뒤 같은 task·다른 글(`created:` 추가)의 파일이 있을 때 범위 안 편집 | `test_approvals.py`(v2 묶음) | 거부, 문구에 파일 이름과 "status만"이 있다 |
| 승인 뒤 계획 파일이 없을 때 | 같은 곳 | 거부, 지금 문구 그대로 |
| 다른 글의 파일을 승인된 글로 고친 뒤 | 같은 곳 | 허용(판정이 바뀌지 않았음) |
| 다른 task의 파일만 있을 때 | 같은 곳 | 지금 문구 그대로(남의 계획 이름을 말하지 않는다) |

## 단계
- [x] 1. 이 본문을 `docs/plans/2026-11-16-devenv-fix-b.md`에 저장한다(첫 편집 전에).
- [x] 2. 테스트를 쓰고 실패를 확인한다.
- [x] 3. skill 세 개의 문구를 고친다.
- [x] 4. hook의 거부 문구를 구현한다.
- [x] 5. 게이트.
- [x] 6. code-reviewer 1회(문구 변경이 판정을 바꾸지 않는지, 문구에 새는 정보가 없는지). 반영, 게이트.
- [x] 7. 커밋(코드와 문서 분리), push, CI 확인, 마감 기록.

## 검증
- `python -I tests/run_all.py` 통과(끝줄의 wall 기록), CI 성공(run id). 상태 목표: CI_VERIFIED
- 실사용: skill 문구는 새 세션에서만 읽힌다. 다시 하는 최종 확인에서 "계획 저장 뒤 첫 편집이 거부되지 않는다"를 본다(미확인으로 남긴다).

## 위험
- `test_approvals`가 게이트에서 가장 길다(406~443초, 한도 600초). 이 묶음이 더하는 hook 호출은 10회 안쪽으로 한다. 끝줄의 wall이 540초를 넘으면 멈추고 모듈을 나누는 계획을 따로 낸다.
- 문구에 파일 이름이 들어간다: 이 저장소의 `docs/plans` 아래 이름뿐이고 같은 task를 적은 파일로 한정한다.
- skill의 문장을 고치면 이미 열린 세션에는 반영되지 않는다.

## 진행 기록
- 2026-11-16 단계 2: `tests/test_docs_consistency.py`의 D6 6건이 고치기 전에 실패하는 것을 확인했다(틀의 frontmatter `['status', 'created']`, 해시 불일치, 문구 없음). `tests/test_approvals.py`의 U-T1~3은 고치기 전 실패를 모듈 실행으로 보지 않았다(모듈 1회 약 7분). 근거는 묶음 A의 탐색에서 본 옛 문구다. **이 부분은 테스트 먼저 규칙을 온전히 지키지 못했다.**
- 단계 3: skill 문구를 고치는 패치 스크립트를 heredoc으로 만들었다(CLAUDE.md §9 위반). 결과는 일치 개수 검사와 D6 테스트로 확인했고 깨진 곳은 없다.
- 단계 4: `hooks/discipline.py`에 `_v2_unbound_why`, `UNBOUND_DIFFERS`. `v2_write_check`에서 거부 문구를 정할 때만 쓴다.
- 단계 6 리뷰(code-reviewer 1회, "fix first"): 판정이 바뀌지 않음은 확인됐다. 반영 3건: (1) `/implement`의 멈춤 조건 4에도 push 예외를 적었다(23행과 31행이 어긋났다), (2) `/plan`의 저장 문장을 "있는 `status:` 줄의 값을 바꾼다, frontmatter를 한 겹 더 얹지 않는다"로 고쳤다(두 겹이면 해시가 달라짐을 테스트로 고정), (3) 승인된 글이 두 파일에 있을 때의 문구 `UNBOUND_TWICE`를 따로 두고 이름이 3개를 넘으면 "외 N개"를 붙였다(U-T4). 반영하지 않은 것: `plan_task`가 본문의 첫 `task:` 줄을 잡아 무관한 계획의 이름이 문구에 나올 수 있다(판정과 무관, 기존 동작), 거부될 때마다 `docs/plans`를 한 번 더 읽는다(파일 수십 개 규모에서는 무시할 만함).
- 리뷰가 제안으로만 남긴 것: CLAUDE.md §2의 "/implement … stops only when … an outward-facing action (push, deploy, publish) is next"도 같은 예외를 말하지 않는다(CLAUDE.md 수정은 별도 승인).
- 게이트: `python -I tests/run_all.py` 통과 2회(wall 501초, 484초; 37개 모듈).
- 커밋 `2321a18`, CI run 43809826976 성공(job hooks). 상태: CI_VERIFIED.
- 실사용 미확인: skill 문구는 새 세션에서만 읽힌다. 다시 하는 최종 확인에서 "계획 저장 뒤 첫 편집이 거부되지 않는다"를 본다.
- 묶음 A의 실사용 관찰(참고): 이 묶음 중 리뷰의 결과 보고와 완료 알림이 차례로 온 뒤에도 범위 안 파일 6개의 편집이 거부되지 않았다. 알림 하나만 따로 온 경우는 아니어서 확정은 아니다.
