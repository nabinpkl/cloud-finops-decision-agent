# Cloud FinOps Assistant Web UI

Next.js frontend for the local citation-backed cloud pricing assistant. The
browser talks to the backend through the same-origin `/assistant` path; the
rewrite target is controlled by `BACKEND_ORIGIN` in `next.config.js`.

## Run

```bash
pnpm --dir web install
just web
```

The dev server listens on <http://localhost:3000>. Keep model keys and pricing
fetch credentials on the backend side only; this app should not expose them via
`NEXT_PUBLIC_*` variables.
