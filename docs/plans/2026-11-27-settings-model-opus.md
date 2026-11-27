---
status: done
task: settings-model-opus
---

# settings.json의 model을 opus로 저장소에 반영

- 릴리스와 push: **이 계획의 승인은 릴리스(`merge --ff-only`)와 `git push origin main`, CI 확인까지 포함한다** (사용자 결정 2026-11-27: "승인 및 push 와 release 진행", "반영해"). 강제 push는 하지 않는다.

## 목표
운영 중인 `~/.claude/settings.json`에 커밋되지 않고 남은 변경(`/model`로 바뀐 `"model": "opus"`와 Claude Code가 다시 쓴 키 순서)을 작업 트리 `C:\dev\_wt\claude-config`에 그대로 옮겨 커밋하고 릴리스해서, `~/.claude`에 미커밋 변경이 남지 않게 한다. 비목표: 다른 설정 키 변경, hook 변경, 작업 트리의 `docs/plans/2026-11-24-monitor-v1-2.md` 미커밋 변경(그대로 둔다).

## 변경 범위
- C:/dev/_wt/claude-config/settings.json
- C:/dev/_wt/claude-config/docs/plans/**

## 조사 결과
- JSON으로 비교하면 `HEAD:settings.json`과 운영 파일이 다른 키는 `model` 하나다(`fable` → `opus`). 나머지는 최상위 키 순서만 다르다(운영 파일: `cleanupPeriodDays, env, permissions, model, hooks, enabledPlugins, …`).
- 작업 트리 HEAD = `origin/main` = `~/.claude` HEAD = `807c7fc`. `~/.claude`의 미커밋 변경은 `settings.json` 하나다.
- 최상위 계획 파일이 10개로 상한이다(`tests/test_docs_consistency.py` D10). 닫힌 계획 `2026-11-25-distill-skill-wording.md`(status: done)를 `archive/`로 옮긴다. 그 파일 말고는 저장소 어디에서도 그 경로를 가리키지 않는다(`git grep`).

## 가정
- 운영 파일을 바이트 그대로 복사한다(키 순서 포함). 그래야 merge 뒤 `~/.claude`에서 `settings.json`의 diff가 없다. 복사 전후로 줄바꿈 방식이 같은지 확인한다.
- `~/.claude`에서 `merge --ff-only`가 미커밋 `settings.json` 때문에 거부되면, 들어올 내용과 운영 파일이 같다는 것(해시)을 확인한 뒤 `git -C ~/.claude checkout -- settings.json`으로 되돌리고 merge한다. 결과 내용은 지금과 같다.

## 단계
- [x] 1. 이 계획을 `docs/plans/2026-11-27-settings-model-opus.md`에 저장(`status: in-progress`). `git mv docs/plans/2026-11-25-distill-skill-wording.md docs/plans/archive/` — 검증: 승인본과 status 줄만 다름, 최상위 10개.
- [x] 2. `cp ~/.claude/settings.json settings.json` 뒤 `git hash-object`가 운영 파일과 같고, JSON 비교에서 HEAD와 다른 키가 `model`뿐임을 확인 — 검증: 해시 일치, 키 차이 `['model']`.
- [x] 3. 게이트 `python -I tests/run_all.py`(포그라운드) — 검증: `GATE PASSED`.
- [x] 4. 진행 기록을 쓰고 마지막 편집으로 `status: done`, 게이트를 다시 실행 — 검증: `GATE PASSED`.
- [ ] 5. /commit으로 커밋(settings / 계획서와 보관 이동) → /release-hooks(`merge --ff-only`, `~/.claude`에서 게이트) → `git push origin main` → CI 확인 — 검증: `~/.claude` `git status` 깨끗함, CI run id와 success.

## 리스크와 대응
- `settings.json`은 모든 세션의 hook 설정을 담는다. 바뀌는 값은 `model` 하나라 hook 동작은 그대로다. 잠기면 CLAUDE.md의 Release 절대로 마지막 정상 커밋의 `settings.json`을 되돌린다.
- 되돌리기: 커밋을 revert하면 된다.

## 영향 범위
기본 모델이 저장소에서도 `opus`가 된다. 다른 설정과 문서는 바뀌지 않는다.

## 진행 기록
<!-- /implement appends one line per step: date, step, commit sha, deviations -->
- 2026-11-27 단계 1: 계획 저장, `2026-11-25-distill-skill-wording.md`를 archive로 이동, 최상위 10개 — 계획대로
- 2026-11-27 단계 2: 운영 파일 복사, `git hash-object` 둘 다 `1120a75`, HEAD와의 키 차이 `['model']` — 계획대로
- 2026-11-27 단계 3·4: 편집이 이것뿐이라 게이트는 `status: done` 뒤 한 번으로 합침(두 번 돌려도 같은 내용) — 결과는 보고와 커밋 메시지에 적음
