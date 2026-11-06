---
paths:
  - "**/*.{ts,tsx,js,jsx,mjs,cjs}"
  - "**/package.json"
  - "**/tsconfig*.json"
---

# TypeScript / Node rules (applies to *.ts, *.tsx, *.js)

## Project & tooling
- `pnpm` only (`pnpm add`, `pnpm add -D`, `pnpm exec`, `pnpm dlx`); commit `pnpm-lock.yaml`. Node 24, ESM (`"type": "module"`), `NodeNext` module resolution: relative imports carry the `.js` extension.
- TypeScript stays on 6.x until typescript-eslint supports TS 7.
- Quality gate: `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`.

## Types
- `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` stay on. No `any`; use `unknown` + narrowing. No non-null `!` assertions or `as` casts to silence errors. A cast needs a comment explaining why it is safe.
- Model domain states with discriminated unions (`{ kind: "ok", ... } | { kind: "error", ... }`) and exhaustive `switch` with a `never` check.
- Boundary parsing with `zod`: `const Input = z.object({...}); type Input = z.infer<typeof Input>;` for HTTP bodies, env, files and LLM/tool output. Env is parsed once at startup.
- Prefer `readonly` properties/arrays for domain data; `as const` for literal tables.
- Branded types for IDs that must not mix (`type UserId = string & { readonly __brand: "UserId" }`).

## Structure
- `src/domain` (pure logic, types, errors), `src/adapters` (db, http, fs, clock), `src/app` (use cases), `src/main.ts` (composition root). Interfaces for adapters are plain TS `interface`s, injected via function params/constructors (no DI framework unless the project already has one).
- Named exports only; no default exports. One concept per module; avoid barrel files that create cycles.

## Errors
- `class AppError extends Error { constructor(message: string, options?: ErrorOptions) { super(message, options); this.name = new.target.name; } }` + subclasses. Wrap with `{ cause: err }`.
- In `catch (err: unknown)`, narrow before use. Never empty `catch {}`; never `.catch(() => {})`.
- No floating promises (lint enforced): every promise is awaited, returned, or explicitly `void`ed with a comment.
- `fetch`/HTTP with `AbortSignal.timeout(ms)`. Retries are explicit and bounded, with backoff + jitter, only for idempotent requests.

## Logging
- `pino` with `redact: ["*.password", "*.token", "*.secret", "headers.authorization", ...]`; child loggers for request context; `logger.info({ orderId, durationMs }, "order.created")`. No `console.log` outside CLI output and scripts.

## Testing
- `vitest`: `describe/it` named by behavior; `vi.useFakeTimers()` for time; no real network (inject a fake adapter, or use `msw` at the HTTP boundary). `fast-check` for property tests of parsers and invariants.
- Coverage: `pnpm exec vitest run --coverage` (v8).

## Common mistakes to avoid
- `==` instead of `===`; `forEach` with async callbacks (use `for...of` + `await` or `Promise.all`); unhandled rejections; `Date` without explicit timezone handling; `JSON.parse` without schema validation; mutating function arguments; `process.env.X!`; path joins with string concatenation (use `node:path`); `__dirname` in ESM (use `import.meta.dirname`).
