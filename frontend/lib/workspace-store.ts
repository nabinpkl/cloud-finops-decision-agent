"use client";

import { create } from "zustand";

import type { CompareResult } from "@/components/tools/compare-result-types";
import { runCompare } from "@/lib/compare-client";

// A spec the human committed by pressing Compare. Family/region values are the
// backend's canonical vocabulary so they post verbatim.
export type ViewSpec = {
  vcpu: number;
  ram_gb: number;
  family: string;
  region: string;
};

type WorkspaceState = {
  // agent panel open/closed — toggled from the navbar Ask AI button. The panel
  // and the navbar live in different React trees (the navbar is outside the
  // AG-UI provider); this module-singleton store is their only shared channel.
  agentOpen: boolean;
  toggleAgent: () => void;
  setAgent: (open: boolean) => void;

  // committed comparison spec + its deterministic result. The dashboard renders
  // `result`; the agent panel reads `view` for grounding but never writes here.
  view: ViewSpec | null;
  result: CompareResult | null;
  status: "idle" | "loading" | "error";
  error: string | null;
  compare: (spec: ViewSpec) => Promise<void>;
  reset: () => void;
};

export const useWorkspace = create<WorkspaceState>((set) => ({
  agentOpen: false,
  toggleAgent: () => set((s) => ({ agentOpen: !s.agentOpen })),
  setAgent: (open) => set({ agentOpen: open }),

  view: null,
  result: null,
  status: "idle",
  error: null,
  compare: async (spec) => {
    set({ status: "loading", error: null, view: spec });
    try {
      const result = await runCompare(spec);
      set({ result, status: "idle" });
    } catch (e) {
      set({
        status: "error",
        error: e instanceof Error ? e.message : "comparison failed",
      });
    }
  },
  reset: () => set({ view: null, result: null, status: "idle", error: null }),
}));
