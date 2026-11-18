# P5-01: 권한 확인 창이 실제로 뜨는지 확인하는 절차 (사용자가 실행)

- 목적: `settings.json`의 ask 규칙(playwright 조작 도구 13개, github 쓰기 도구 18개, 그리고 임의 실행 명령)이 **auto mode에서도 확인 창을 띄우는지** 본다. 지난 최종 확인에서 playwright 조작 도구가 54회 실행됐는데, 창이 떴는지는 화면을 본 사람만 안다.
- 근거: `docs/plans/2026-11-16-devenv-p5.md`의 S3. 공식 문서(permission-modes)는 "Explicit ask rules still force a prompt"라고 한다. 한편 `C:\dev\.claude\settings.local.json`에 `mcp__playwright__browser_type`, `browser_evaluate`, `browser_click`, `browser_press_key`의 allow 4개가 있다(창에서 "항상 허용"을 고른 흔적으로 보인다). 그 4개가 `C:\dev`에서 ask를 덮는지가 두 번째로 볼 것이다.
- 소요: 10분 안쪽. 두 번 한다.

## A. `C:\dev`에서
1. 새 터미널에서 `cd C:\dev` 후 `claude`를 실행한다. 평소 쓰는 권한 모드 그대로 둔다(화면 아래에 표시되는 모드를 적어 둔다).
2. 이렇게 시킨다:
   ```
   playwright로 https://example.com 을 열고, 페이지의 링크를 클릭한 다음, 클릭 뒤의 주소를 알려 줘.
   ```
3. 적는다:
   - `browser_navigate`(열기)에서 창이 떴는가 → 규칙상 allow라 뜨지 않아야 한다.
   - `browser_click`(클릭)에서 창이 떴는가 → user 설정으로는 ask, `C:\dev`의 local 설정으로는 allow다. **여기서 무엇이 이기는지**가 답이다.
4. 이어서 이렇게 시킨다(local 설정에 없는 조작 도구):
   ```
   같은 페이지에서 링크 위에 마우스를 올려(hover) 봐.
   ```
   - `browser_hover`에서 창이 떴는가 → ask이고 local allow가 없다. 뜨면 ask 규칙이 auto mode에서도 동작한다는 뜻이다.
5. 창이 뜨면 **"이번만 허용"** 을 고른다("항상 허용"을 고르면 local 설정에 allow가 더해져 다음 확인이 흐려진다).

## B. 다른 폴더에서
1. 새 터미널에서 `C:\dev` **밖**의 폴더로 간다(예: `cd $env:TEMP`) 후 `claude`를 실행한다. local 설정의 영향이 없는 자리다.
2. A의 2~4를 그대로 한다.
3. 적는다: `browser_click`과 `browser_hover`에서 각각 창이 떴는가.

## C. 임의 실행 명령 (2026-11-16의 허용 축소 뒤)
아무 폴더의 세션에서 이렇게 시킨다:
```
node -e "console.log(1+1)" 를 실행해 줘.
```
- 창이 떴는가 → `Bash(node:*)`가 ask로 옮겨졌으므로 떠야 한다.

## 결과를 알려 주는 방법
평소 쓰는 세션에 이렇게 적는다(해당 없는 것은 비워 둔다):
```
P5-01 결과. 모드: <auto / 기본 / …>
A(C:\dev): click 창 <떴다/안 떴다>, hover 창 <떴다/안 떴다>
B(다른 폴더): click 창 <떴다/안 떴다>, hover 창 <떴다/안 떴다>
C: node 창 <떴다/안 떴다>
```

## 결과에 따라 할 일 (그 세션이 계획으로 올린다)
| 결과 | 뜻 | 다음 |
|---|---|---|
| B에서 둘 다 뜸, A에서 click만 안 뜸 | ask 규칙은 동작한다. `C:\dev`의 local allow 4개가 ask를 덮는다 | `C:\dev\.claude\settings.local.json`의 allow 4개를 지우는 것을 승인받는다 |
| B에서도 안 뜸 | auto mode에서 ask 규칙이 창을 띄우지 않는다 | ask로는 막을 수 없으므로 PreToolUse hook으로 확인을 요구하는 안을 P5 항목으로 올린다 |
| 전부 뜸 | 규칙이 의도대로 동작한다. local allow는 덮지 않는다 | P5-01을 닫는다. local allow 4개는 쓸모가 없으니 지우는 것을 제안한다 |
