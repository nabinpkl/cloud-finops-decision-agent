"use client";

import { useSyncExternalStore } from "react";

/**
 * A tiny external store carrying the server-trusted `sessionLimitReached` flag
 * (ADR-0011) out of the transport converter and into the SessionLimitBanner.
 *
 * Why this instead of `useAssistantTransportState`: in this assistant-ui
 * version the transport "extras" (which carry the round-tripped state and the
 * symbol that hook asserts) do not propagate onto `thread.extras`, so the hook
 * throws on every render. The converter, by contrast, receives the raw
 * transport `state` reliably on each update, so it is the dependable place to
 * read the flag. The frontend still never *writes* the flag (the backend owns
 * it); the converter only mirrors what the server sent.
 */

let reached = false;
const listeners = new Set<() => void>();

export function publishSessionLimitReached(value: boolean): void {
  if (value === reached) return;
  reached = value;
  // Notify after the current render pass: the converter runs during the
  // runtime's render, and synchronously notifying listeners would update the
  // banner while another component is rendering.
  queueMicrotask(() => {
    for (const listener of listeners) listener();
  });
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useSessionLimitReached(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => reached,
    () => false,
  );
}
