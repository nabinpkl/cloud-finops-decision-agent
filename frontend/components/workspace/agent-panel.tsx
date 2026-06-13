"use client";

import { AuiProvider, Suggestions, useAui } from "@assistant-ui/react";
import { Sparkles } from "lucide-react";
import { useMemo } from "react";

import { SessionLimitBanner } from "@/components/SessionLimitBanner";
import { Thread } from "@/components/assistant-ui/thread";
import { ComparisonTable } from "@/components/tools/comparison-table";
import { ContextStrip } from "@/components/workspace/context-strip";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace-store";

// The agent as a SEPARATE, on-demand panel docked below the navbar. It mounts
// inside the AG-UI runtime provider (see page.tsx), so the Thread, suggestions,
// and the `compare` tool-UI all work. It is decoupled from the manual table:
// it answers with cited results (its own compare tool calls render inline) and
// never mutates the dashboard.
export function AgentPanel() {
  const agentOpen = useWorkspace((s) => s.agentOpen);
  const setAgent = useWorkspace((s) => s.setAgent);

  const suggestions = useMemo(
    () =>
      Suggestions([
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
    [],
  );

  const aui = useAui({ suggestions });

  return (
    <AuiProvider value={aui}>
      {/* Registers the `compare` tool-UI renderer inside the thread; draws
          nothing itself. */}
      <ComparisonTable />

      <aside
        aria-label="Pricing assistant"
        className={cn(
          "bg-card fixed top-[var(--top-h)] right-0 bottom-0 z-40 flex w-[var(--panel-w)] max-w-[100vw] flex-col border-l shadow-[-8px_0_24px_-12px_rgba(0,0,0,0.16)]",
          "transition-transform duration-[260ms] ease-[cubic-bezier(.32,.72,0,1)]",
          agentOpen ? "translate-x-0" : "translate-x-full",
        )}
      >
        <header className="from-brand/10 relative border-b bg-gradient-to-br to-transparent p-4">
          <div className="flex items-center gap-3">
            <span className="from-brand inline-flex size-[34px] shrink-0 items-center justify-center rounded-[11px] bg-gradient-to-br to-fuchsia-500 text-white shadow">
              <Sparkles className="size-[19px]" />
            </span>
            <div className="flex flex-col leading-tight">
              <span className="font-semibold">Pricing assistant</span>
              <span className="text-muted-foreground flex items-center gap-1.5 text-[11.5px]">
                <span className="bg-ok size-1.5 rounded-full" />
                online · cited answers
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setAgent(false)}
            aria-label="Close panel"
            className="text-muted-foreground hover:text-foreground hover:bg-muted absolute top-3 right-3 inline-flex size-7 items-center justify-center rounded-md"
          >
            ✕
          </button>
          <p className="text-muted-foreground mt-3 text-[11.5px]">
            Cited answers from snapshot data. It reads your current view but does
            not change the table beside it.
          </p>
          <ContextStrip />
        </header>

        <SessionLimitBanner />

        <div className="min-h-0 flex-1 overflow-hidden">
          <Thread />
        </div>
      </aside>
    </AuiProvider>
  );
}
