"use client";

import { SessionLimitBanner } from "@/components/SessionLimitBanner";
import { Thread } from "@/components/assistant-ui/thread";
import { useAui, AuiProvider, Suggestions } from "@assistant-ui/react";
import { MyRuntimeProvider } from "./MyRuntimeProvider";

function ThreadWithSuggestions() {
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
      <Thread />
    </AuiProvider>
  );
}

export default function Home() {
  return (
    <MyRuntimeProvider>
      <div className="flex h-full flex-col">
        <SessionLimitBanner />
        <div className="flex-1 overflow-hidden">
          <ThreadWithSuggestions />
        </div>
      </div>
    </MyRuntimeProvider>
  );
}
