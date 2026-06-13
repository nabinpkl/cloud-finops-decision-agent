"use client";

import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { useEffect, useMemo, type ReactNode } from "react";

import { publishSessionLimitReached } from "@/lib/session-limit";
import { useWorkspace } from "@/lib/workspace-store";

type RunInput = Parameters<HttpAgent["run"]>[0];

// HttpAgent that grounds every run in the manual dashboard's committed view by
// forwarding it as RunAgentInput.forwardedProps.currentView. run(input) is the
// wire entry point, so this catches every request regardless of how the adapter
// triggers it; reading the store at run time keeps it current, never stale. The
// backend RE-VALIDATES this (CompareQueryArgs) and drops anything malformed — it
// is an untrusted grounding hint, not authoritative, and never mutates the table.
class GroundedHttpAgent extends HttpAgent {
  run(input: RunInput) {
    const view = useWorkspace.getState().view;
    if (view) {
      input.forwardedProps = {
        ...(input.forwardedProps ?? {}),
        currentView: {
          vcpu: view.vcpu,
          ram_gb: view.ram_gb,
          family: view.family,
          region: view.region,
        },
      };
    }
    return super.run(input);
  }
}

// AG-UI transport (ADR-0016). The backend is an AG-UI server: POST /assistant
// streams AG-UI events (RUN_STARTED, TEXT_MESSAGE_*, TOOL_CALL_*, STATE_SNAPSHOT,
// RUN_FINISHED). The HttpAgent client carries the wire; the assistant-ui AG-UI
// runtime adapter renders text + tool-call parts so the existing
// ComparisonTable Tool UI keeps working unchanged. State is backend-authoritative:
// the frontend renders the STATE_SNAPSHOT view-state, never owns it. The
// transport is snapshot-only (one full STATE_SNAPSHOT per turn; no STATE_DELTA).

// Same-origin path; next.config.js rewrites /assistant to the backend so the
// browser never sees the backend URL and CORS is avoided.
const ASSISTANT_URL = "/assistant";

type ViewState = {
  // Server-trusted per-session token-cap flag (ADR-0011). The backend writes it
  // into the AG-UI state snapshot; the frontend mirrors it into the store the
  // SessionLimitBanner reads. The frontend never writes this field.
  sessionLimitReached?: boolean;
};

export function MyRuntimeProvider({ children }: { children: ReactNode }) {
  const agent = useMemo(() => new GroundedHttpAgent({ url: ASSISTANT_URL }), []);

  const runtime = useAgUiRuntime({
    agent,
    onError: (e: Error) => {
      // Surface transport errors to the console; the chat shell shows the
      // backend-rendered error text from the stream itself.
      console.error("ag-ui transport error", e);
    },
  });

  // Mirror the server-trusted session-limit flag from the backend-authoritative
  // view-state into the banner store. The HttpAgent applies the per-turn
  // STATE_SNAPSHOT to agent.state; subscribe to read it.
  useEffect(() => {
    const sub = agent.subscribe({
      onStateChanged: ({ state }: { state: unknown }) => {
        const view = (state ?? {}) as ViewState;
        publishSessionLimitReached(view.sessionLimitReached === true);
      },
    });
    return () => sub.unsubscribe();
  }, [agent]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
