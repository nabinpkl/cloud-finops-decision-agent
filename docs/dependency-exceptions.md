# Dependency exceptions

Dependencies that do not clear the standard bar (latest release recent, mature,
human-maintained, healthy issue triage) but are accepted anyway, with the risk
and the trigger to revisit. See the dependency rules in the worker guidelines.

## assistant-stream (Python)

- **Version accepted:** 0.0.32 (2026-05).
- **Bar it misses:** pre-1.0 (`0.0.x`), young package, small surface.
- **Why accepted:** it is the only implementation of assistant-ui's
  assistant-transport protocol on the backend, published by the assistant-ui
  maintainers themselves (same org as the frontend `@assistant-ui/react` we
  already depend on). There is no maintained alternative; hand-rolling the
  wire protocol would be more risk, not less.
- **Risk accepted:** the protocol/API may change across `0.0.x` releases and
  break the `POST /assistant` bridge in `api/transport.py`. Surface is small, so
  a break is cheap to chase.
- **Revisit trigger:** drop this exception when assistant-stream reaches a `0.1+`
  / stable line, or if a break lands. Re-pin and re-test the bridge then.
