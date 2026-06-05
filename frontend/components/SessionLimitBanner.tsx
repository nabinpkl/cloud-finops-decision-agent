"use client";

/**
 * Sticky banner shown when the per-session token cap (ADR-0011) is hit.
 *
 * The flag arrives in the transport state as `sessionLimitReached: true`,
 * written by the backend's session-cap branch in `api/transport.py`. The
 * "Start new conversation" button clears the `finops_session_id` cookie
 * client-side and reloads the page; the next request lands without a
 * cookie, so the backend issues a fresh session id and the per-session
 * counter starts at zero. The global daily and per-client rate caps still
 * apply, so this is not a budget bypass — it is just the user-facing way
 * to restart after a legitimate stop.
 */

import { Button } from "@/components/ui/button";
import { useSessionLimitReached } from "@/lib/session-limit";

export function SessionLimitBanner() {
  // The flag is mirrored out of the transport converter into a small store
  // (frontend/lib/session-limit.ts) because this assistant-ui version does not
  // propagate the transport extras that `useAssistantTransportState` requires.
  const reached = useSessionLimitReached();
  if (!reached) return null;

  const onReset = () => {
    // Clear the cookie client-side so the next request lands without one;
    // the backend will issue a fresh session id via Set-Cookie. Reload to
    // reset the runtime state (messages, sessionLimitReached flag).
    document.cookie = "finops_session_id=; Max-Age=0; path=/";
    window.location.reload();
  };

  return (
    <div
      role="alert"
      className="bg-destructive/10 text-destructive border-destructive/40 flex items-center justify-between gap-4 border-b px-4 py-3"
    >
      <span className="text-sm">
        This conversation reached its token limit. Start a new conversation to continue.
      </span>
      <Button
        type="button"
        variant="destructive"
        size="sm"
        onClick={onReset}
        aria-label="Start a new conversation"
      >
        Start new conversation
      </Button>
    </div>
  );
}
