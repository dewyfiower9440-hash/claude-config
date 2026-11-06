---
paths:
  - "**/*.{ps1,psm1,psd1}"
---

# PowerShell rules (applies to *.ps1, *.psm1, *.psd1)

## Baseline
- Target Windows PowerShell 5.1 unless the project states 7+. Do not use 7-only syntax (`??`, `?:`, `&&`/`||` pipeline chains, `-Parallel`, `ForEach-Object -Parallel`) in 5.1 code.
- Every script/module starts with `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'` (scripts) so errors are terminating.
- Quality gate: `Invoke-ScriptAnalyzer -Path . -Recurse -Settings ./PSScriptAnalyzerSettings.psd1` (zero Error/Warning) and `Import-Module Pester -MinimumVersion 5.0; Invoke-Pester -Output Detailed`.

## Functions
- Approved verbs (`Get-Verb`), `Verb-Noun` singular nouns, `[CmdletBinding()]`, `[OutputType()]`, typed and validated params (`[Parameter(Mandatory)]`, `[ValidateNotNullOrEmpty()]`, `[ValidateSet()]`, `[ValidateScript()]`): that is the boundary validation.
- State-changing functions declare `SupportsShouldProcess` and call `$PSCmdlet.ShouldProcess(...)`, so `-WhatIf`/`-Confirm` work.
- Return objects (`[pscustomobject]@{...}`), not formatted text. No `Format-*` inside functions; formatting is the caller's job.
- Pure logic in functions that take inputs and return outputs. Keep I/O (`Invoke-RestMethod`, file, registry, remote sessions) in separate small functions that can be mocked in Pester.

## Errors
- Use `throw` / `$PSCmdlet.ThrowTerminatingError()` with a clear message (what, on which target, why). `try { } catch { }` must handle or rethrow (`throw`), never swallow. Native exe calls: check `$LASTEXITCODE` explicitly after each call.
- `Invoke-RestMethod`/`Invoke-WebRequest` with `-TimeoutSec`, and `-UseBasicParsing` on 5.1. Bounded retries with backoff only for idempotent calls.

## Output & logging
- `Write-Verbose` for diagnostics, `Write-Information` for progress, `Write-Warning` for anomalies, `Write-Error` for non-terminating errors. No `Write-Host` (except interactive UX scripts).
- Never write secrets to the pipeline or logs; use `[SecureString]`/`PSCredential` and Credential Manager. No plaintext passwords in parameters.

## Encoding & files
- 5.1 `Set-Content`/`Out-File -Encoding utf8` writes a BOM, which breaks TOML/JSON/sh consumers. Use `[IO.File]::WriteAllText($p, $text, [Text.UTF8Encoding]::new($false))` for BOM-less UTF-8. Save `.ps1` files containing non-ASCII as UTF-8 **with** BOM (5.1 reads BOM-less files as ANSI).
- Paths: `Join-Path`, `Resolve-Path`, `-LiteralPath` for user-supplied paths.

## Testing (Pester 5)
- `BeforeAll { Import-Module "$PSScriptRoot/../src/X/X.psd1" -Force }`, `Describe/Context/It` named by behavior, `Mock` for I/O functions with `-ModuleName`, `Should -Invoke` for interaction checks only at boundaries, `TestDrive:` for files.

## Common mistakes to avoid
- Relying on `$?` after native commands piped through PowerShell; unintended pipeline output (assign or `[void]` noisy calls like `.Add()`); `-eq` on arrays acting as a filter; `$null` on the right side of comparisons (put `$null -eq $x`); aliases (`%`, `?`, `ls`) in scripts; `Invoke-Expression` on input; global variables for state.
