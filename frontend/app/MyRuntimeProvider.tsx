"use client";

import { HttpAgent } from "@ag-ui/client";
import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { useEffect, useMemo, type ReactNode } from "react";

import { publishSessionLimitReached } from "@/lib/session-limit";

// AG-UI transport (ADR-0016). The backend is an AG-UI server: POST /assistant
// streams AG-UI events (RUN_STARTED, TEXT_MESSAGE_*, TOOL_CALL_*, STATE_SNAPSHOT,
// RUN_FINISHED). The HttpAgent client carries the wire; the assistant-ui AG-UI
// runtime adapter renders text + tool-call parts so the existing
// ComparisonTable Tool UI keeps working unchanged. State is backend-authoritative:
// the frontend renders the STATE_SNAPSHOT/STATE_DELTA view-state, never owns it.

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
  const agent = useMemo(() => new HttpAgent({ url: ASSISTANT_URL }), []);

  const runtime = useAgUiRuntime({
    agent,
    onError: (e: Error) => {
      // Surface transport errors to the console; the chat shell shows the
      // backend-rendered error text from the stream itself.
      console.error("ag-ui transport error", e);
    },
  });

  // Mirror the server-trusted session-limit flag from the backend-authoritative
  // view-state into the banner store. The HttpAgent applies STATE_SNAPSHOT /
  // STATE_DELTA to agent.state; subscribe to read it.
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
