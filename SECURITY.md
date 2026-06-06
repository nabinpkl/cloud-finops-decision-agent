# Security Policy

## Supported Versions

This project is experimental and pre-release. Security fixes land on the default branch only until tagged releases exist. There is no long-term support policy yet.

## Reporting a Vulnerability

Use GitHub private vulnerability reporting for this repository if it is enabled. If private reporting is not available yet, contact the maintainer out of band and avoid posting exploit details, secrets, trace payloads, or proof-of-concept abuse in a public issue.

Please include:

- A short impact summary.
- Affected component or path, such as `backend/src/api/`, `backend/src/ingest/`, `backend/src/normalize/`, or `frontend/`.
- Reproduction steps using local fixtures or redacted inputs.
- Whether the issue exposes credentials, trace content, pricing snapshots, model-provider keys, budget state, or user prompts.

Expected response for now: acknowledgement as soon as practical, triage on the default branch, and a public fix note after sensitive details are no longer useful to attackers.

## Security Scope

In scope:

- Secret exposure through `.env`, Infisical state, traces, logs, SQLite budget state, or frontend bundles.
- Public API abuse paths around `/assistant`, budget enforcement, CORS, proxy trust, and model-provider credentials.
- Citation or snapshot handling bugs that can make the agent quote unverifiable prices as verified.
- Dependency or packaging issues that materially affect users running the service.

Out of scope:

- Provider catalog prices being wrong upstream.
- Stale local snapshots when the freshness policy correctly marks them stale.
- General product bugs with no confidentiality, integrity, availability, or supply-chain impact.

## Local Data Policy

Do not commit local runtime data. `.env`, `.infisical.json`, `store/`, `var/`, trace JSONL files, and SQLite databases are local artifacts. Pricing snapshots can be re-fetched; traces and budget databases may contain sensitive operational data.

When sharing bug reports, redact provider API keys, model-provider keys, prompt content, trace attributes, IP-derived identifiers, and any local snapshot paths that reveal private machine details.

## Public Deployment Runbook

The `/assistant` endpoint is intentionally unauthenticated in v0. Treat it as a
public model-spending surface and put ordinary edge controls in front of it when
hosting beyond localhost.

Minimum controls for a public deployment:

- Terminate TLS at a reverse proxy, CDN, or platform edge.
- Enforce edge request-rate limits before traffic reaches FastAPI.
- Keep `BUDGET_ENABLED=true`.
- Set `BUDGET_IP_HASH_SALT_SECRET` to a high-entropy secret and rotate it if IP-derived budget IDs may have leaked.
- Set `PUBLIC_DEPLOYMENT=true` and configure `TRUSTED_PROXY_COUNT` /
  `TRUSTED_PROXY_CIDRS` only for proxies you operate.
- Keep `OTEL_CAPTURE_CONTENT=false` unless actively debugging with consent.
- Watch `var/traces/traces.jsonl` and the budget database for policy blocks,
  token spikes, and repeated 429/503 budget responses.

Abuse response:

1. Lower `GLOBAL_DAILY_TOKEN_CAP`, `CLIENT_RATE_REQUESTS_PER_MINUTE`,
   `CLIENT_RATE_TOKENS_PER_HOUR`, and `SESSION_TOKEN_CAP` in `.env`.
2. Restart the API process so the new settings load.
3. Rotate `BUDGET_IP_HASH_SALT_SECRET` if per-client identifiers should be reset.
4. Temporarily disable the model surface by setting `BUDGET_ENABLED=true` with
   very low caps, or by removing the frontend/proxy route to `/assistant`.
5. Preserve `var/traces/` and `var/budgets.db` for incident review, but do not
   publish them.

Reverse proxy example shape:

```nginx
limit_req_zone $binary_remote_addr zone=assistant:10m rate=10r/m;

location /assistant {
    limit_req zone=assistant burst=5 nodelay;
    proxy_pass http://127.0.0.1:8000;
}
```

This edge limit is not a replacement for the backend budget controls. It reduces
load before Python/model work begins; backend caps still enforce token spend and
session limits.
