"use client";

import { useEffect, useState } from "react";
import { SessionLimitBanner } from "@/components/SessionLimitBanner";
import { ComparisonTable } from "@/components/tools/comparison-table";
import { Thread } from "@/components/assistant-ui/thread";
import { useAui, AuiProvider, Suggestions } from "@assistant-ui/react";
import { MyRuntimeProvider } from "./MyRuntimeProvider";

// One AuiProvider wrapping the whole app, bound to the transport runtime.
// useAssistantTransportState (SessionLimitBanner) and useAssistantToolUI
// (ComparisonTable) read the thread's transport extras off the Aui store, so
// they must live INSIDE this provider, not as siblings of it. Previously the
// AuiProvider only wrapped the Thread, so those two threw
// "...only be called when you are using useAssistantTransportRuntime".
function AppShell() {
  const aui = useAui({
    suggestions: Suggestions([
      {
        title: "Cheapest 4 vCPU / 8 GB",
        label: "general-purpose VM in the EU",
        prompt: "Cheapest 4 vCPU 8 GB general-purpose VM in the EU?",
      },
      {
        title: "Compare a spec",
        label: "across AWS, GCP and Azure",
        prompt:
          "Compare a 2 vCPU 4 GB general-purpose VM across AWS, GCP and Azure.",
      },
    ]),
  });
  return (
    <AuiProvider value={aui}>
      {/* Registers the generative-UI renderer for `compare` tool calls; draws
          nothing itself. */}
      <ComparisonTable />
      <div className="flex h-full flex-col">
        <SessionLimitBanner />
        <div className="flex-1 overflow-hidden">
          <Thread />
        </div>
      </div>
    </AuiProvider>
  );
}

export default function Home() {
  // The assistant-transport runtime is client-only: hooks like
  // useAssistantTransportState (SessionLimitBanner) and useAssistantToolUI
  // (ComparisonTable) throw during SSR because no runtime exists on the server.
  // Render nothing until mounted so the runtime tree is client-only; server and
  // first client render agree (both the placeholder), so there is no hydration
  // mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Loading…
      </div>
    );
  }

  return (
    <MyRuntimeProvider>
      <AppShell />
    </MyRuntimeProvider>
  );
}
