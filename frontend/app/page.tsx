"use client";

import { useEffect, useState } from "react";

import { AgentPanel } from "@/components/workspace/agent-panel";
import { Dashboard } from "@/components/workspace/dashboard";
import { PageHeader } from "@/components/workspace/page-header";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace-store";
import { MyRuntimeProvider } from "./MyRuntimeProvider";

// The workspace: a manual deterministic dashboard (primary layer) with the
// agent docked as a separate, on-demand panel. Opening the panel SHIFTS the
// page left (Copilot-style, not a modal — no scrim); both stay visible. The
// navbar (layout.tsx) stays full width above the panel.
const SHIFT =
  "transition-[margin] duration-[260ms] ease-[cubic-bezier(.32,.72,0,1)]";

export default function Home() {
  const agentOpen = useWorkspace((s) => s.agentOpen);

  // Only the AG-UI runtime tree is client-only (its hooks throw during SSR).
  // The manual dashboard does not need the runtime, so it renders immediately;
  // the panel mounts after hydration. Server and first client render agree.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

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

      {mounted && (
        <MyRuntimeProvider>
          <AgentPanel />
        </MyRuntimeProvider>
      )}
    </div>
  );
}
