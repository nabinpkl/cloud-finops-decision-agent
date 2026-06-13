"use client";

import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useWorkspace, type ViewSpec } from "@/lib/workspace-store";

// Option VALUES are the backend's canonical vocabulary (FamilyName literals;
// canonical region buckets the service resolves), so the toolbar posts them
// verbatim and never 422s. Labels are just friendlier text.
const VCPUS = [2, 4, 8, 16];
const RAMS = [4, 8, 16, 32];
const FAMILIES = [
  "general-purpose",
  "compute-optimized",
  "memory-optimized",
  "any",
];
const REGIONS = ["eu-central", "us-east", "ap-southeast"];

const SELECT_CLASS =
  "h-9 min-w-[140px] appearance-none rounded-lg border bg-background px-3 text-[13px] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
        {label}
      </span>
      {children}
    </label>
  );
}

// The MANUAL surface: the human drives the deterministic comparison here. On
// Compare it commits the draft spec to the workspace store, which calls
// /compare. Nothing here talks to the agent.
export function FilterToolbar() {
  const compare = useWorkspace((s) => s.compare);
  const loading = useWorkspace((s) => s.status === "loading");
  const [draft, setDraft] = useState<ViewSpec>({
    vcpu: 4,
    ram_gb: 8,
    family: "general-purpose",
    region: "eu-central",
  });

  return (
    <div className="bg-card mb-4 flex flex-wrap items-end gap-4 rounded-lg border p-4 shadow-sm">
      <Field label="vCPU">
        <select
          className={SELECT_CLASS}
          value={draft.vcpu}
          onChange={(e) =>
            setDraft((d) => ({ ...d, vcpu: Number(e.target.value) }))
          }
        >
          {VCPUS.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </Field>

      <Field label="RAM">
        <select
          className={SELECT_CLASS}
          value={draft.ram_gb}
          onChange={(e) =>
            setDraft((d) => ({ ...d, ram_gb: Number(e.target.value) }))
          }
        >
          {RAMS.map((n) => (
            <option key={n} value={n}>
              {n} GB
            </option>
          ))}
        </select>
      </Field>

      <Field label="Family">
        <select
          className={SELECT_CLASS}
          value={draft.family}
          onChange={(e) => setDraft((d) => ({ ...d, family: e.target.value }))}
        >
          {FAMILIES.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Region">
        <select
          className={SELECT_CLASS}
          value={draft.region}
          onChange={(e) => setDraft((d) => ({ ...d, region: e.target.value }))}
        >
          {REGIONS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>

      <div className="grow" />

      <Button
        className="bg-brand text-brand-foreground hover:bg-brand/90 h-9"
        disabled={loading}
        onClick={() => compare(draft)}
      >
        {loading ? "Comparing…" : "Compare"}
      </Button>
    </div>
  );
}
