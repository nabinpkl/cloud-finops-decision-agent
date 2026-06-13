# E2E — dual-surface workspace

Playwright tests for the manual dashboard + agent panel (R12 / S10).

## Setup (once)

Playwright is not in `package.json` (a registry metadata quirk blocked the
install in the build environment). Add it in yours:

```bash
pnpm add -D @playwright/test
pnpm exec playwright install chromium
```

## Run

```bash
pnpm exec playwright test        # boots `next dev` automatically (webServer)
```

## What runs where

- **FE-only cases** (`workspace shell`) run anywhere: navbar + Ask AI toggle,
  layout-shift panel, context-strip hint.
- **`@backend` cases** need the FastAPI backend at `BACKEND_ORIGIN`
  (default `http://localhost:8000`) with a populated `../store/` so `/compare`
  returns rows. They **self-skip** if `/compare` is unreachable.

## Coverage note

The decoupling test asserts the always-checkable half: opening the agent panel
never mutates the dashboard rows (the table is driven solely by `/compare`). The
full "a real agent turn never changes the dashboard rows" assertion needs a live
model endpoint and is left as a manual check on a running stack.
