"use client";

import { EyeIcon } from "lucide-react";

import { useWorkspace } from "@/lib/workspace-store";

// Grounding cue: shows the agent is looking at the same thing the user is. It
// mirrors the COMMITTED comparison spec (store.view, set on Compare), not the
// toolbar draft, so it never goes stale between a filter edit and a Compare.
// Read-only — it reflects the manual view, it does not drive it.
export function ContextStrip() {
  const view = useWorkspace((s) => s.view);

  if (!view) {
    return (
      <div className="text-muted-foreground bg-muted/50 mt-3 rounded-md px-3 py-1.5 text-[11.5px]">
        Run a comparison and the assistant will ground answers in it.
      </div>
    );
  }

  return (
    <div className="text-brand bg-brand/10 border-brand/20 mt-3 flex items-center gap-2 rounded-md border px-3 py-1.5 text-xs">
      <EyeIcon className="size-3.5 shrink-0 opacity-90" />
      <span className="truncate">
        Looking at{" "}
        <b className="font-semibold">
          {view.vcpu} vCPU · {view.ram_gb} GB · {view.family}
        </b>{" "}
        · {view.region}
      </span>
      <span className="ml-auto shrink-0 text-[10.5px] opacity-80">live</span>
    </div>
  );
}
