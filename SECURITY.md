# Security Policy

## Supported Versions

This project is experimental and pre-release. Security fixes land on the default branch only until tagged releases exist. There is no long-term support policy yet.

## Reporting a Vulnerability

Use GitHub private vulnerability reporting for this repository if it is enabled. If private reporting is not available yet, contact the maintainer out of band and avoid posting exploit details, secrets, trace payloads, or proof-of-concept abuse in a public issue.

Please include:

- A short impact summary.
- Affected component or path, such as `src/api/`, `src/ingest/`, `src/normalize/`, or `web/`.
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
