"use client";

import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace-store";
import { Dashboard } from "@/components/workspace/dashboard";
import { PageHeader } from "@/components/workspace/page-header";

// The workspace: a manual deterministic dashboard (primary layer) with the
// agent docked as a separate, on-demand panel. Opening the panel SHIFTS the
// page left (Copilot-style, not a modal — no scrim); both stay visible. The
// navbar (in layout.tsx) stays full width above the panel.
//
// S1 lays the shell + layout-shift; the dashboard (S5) and the real agent panel
// with its AG-UI runtime (S6) replace the placeholders below.
const SHIFT =
  "transition-[margin] duration-[260ms] ease-[cubic-bezier(.32,.72,0,1)]";

export default function Home() {
  const agentOpen = useWorkspace((s) => s.agentOpen);
  const setAgent = useWorkspace((s) => s.setAgent);

  return (
    <div className="relative h-full">
      <div
        className={cn(
          "h-full overflow-auto",
          SHIFT,
          agentOpen && "mr-[var(--panel-w)]",
        )}
      >
        <PageHeader />
        <Dashboard />
      </div>

      {/* Agent panel — separate, docked below the navbar, slides in on demand. */}
      <aside
        aria-label="Pricing assistant"
        className={cn(
          "bg-card fixed top-[var(--top-h)] right-0 bottom-0 z-40 flex w-[var(--panel-w)] max-w-[100vw] flex-col border-l shadow-[-8px_0_24px_-12px_rgba(0,0,0,0.16)]",
          "transition-transform duration-[260ms] ease-[cubic-bezier(.32,.72,0,1)]",
          agentOpen ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="flex items-center justify-between border-b p-4">
          <span className="font-semibold">Pricing assistant</span>
          <button
            type="button"
            onClick={() => setAgent(false)}
            className="text-muted-foreground hover:text-foreground"
            aria-label="Close panel"
          >
            ✕
          </button>
        </div>
        <div className="text-muted-foreground p-4 text-sm">
          Agent panel mounts here (S6).
        </div>
      </aside>
    </div>
  );
}
