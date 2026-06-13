"use client";

import { ChevronRightIcon, LoaderIcon } from "lucide-react";
import { useState } from "react";

import { CitationAge, CitationSource } from "@/components/tools/citation-cells";
import { CitationExcerpt } from "@/components/tools/citation-excerpt";
import type {
  CompareArgs,
  CompareResult,
} from "@/components/tools/compare-result-types";
import { cn } from "@/lib/utils";

// Pure, runtime-agnostic comparison table. Deliberately free of any
// @assistant-ui dependency so the MANUAL dashboard can render it directly from a
// /compare result without pulling in the agent runtime. The agent tool-UI
// wrapper (comparison-table.tsx) renders this same component, so both surfaces
// are byte-identical. Reads only wire-stripped Citation fields; no store_path.

const ACRONYMS = new Set(["aws", "gcp", "ibm"]);

// Provider ids are lowercase (aws, gcp, vultr). Render acronyms uppercase and
// the rest title-cased, so "aws" -> "AWS" and "vultr" -> "Vultr".
function providerLabel(provider: string): string {
  const lower = provider.toLowerCase();
  if (ACRONYMS.has(lower)) return lower.toUpperCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function usd(n?: number): string {
  return typeof n === "number" ? `$${n.toFixed(2)}` : "-";
}

function gb(n?: number): string {
  return typeof n === "number"
    ? `${Number.isInteger(n) ? n : n.toFixed(1)} GB`
    : "-";
}

type CompareRowType = NonNullable<CompareResult["results"]>[number];

// Stable per-row identity for expand state: provider + instance_type survives
// re-render and re-sort, so an expanded breakdown (and any open CitationExcerpt)
// is not dropped when the result set updates.
function rowKey(row: CompareRowType): string {
  return `${row.provider}:${row.instance_type}`;
}

function RankCell({ rank }: { rank: number }) {
  return (
    <span
      className={cn(
        "inline-flex size-6 items-center justify-center rounded text-xs font-medium",
        rank === 1
          ? "bg-brand text-brand-foreground"
          : "bg-muted text-muted-foreground",
      )}
    >
      {rank}
    </span>
  );
}

export function ComparisonSkeleton({ args }: { args: CompareArgs }) {
  const spec = [
    args.vcpu ? `${args.vcpu} vCPU` : null,
    args.ram_gb ? `${args.ram_gb} GB` : null,
    args.region ?? null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="aui-compare-skeleton text-muted-foreground flex items-center gap-2 rounded-lg border px-4 py-3 text-sm">
      <LoaderIcon className="size-4 shrink-0 animate-spin" />
      <span>Comparing providers{spec ? ` for ${spec}` : ""}...</span>
    </div>
  );
}

// The agent-derived tier (synthesized GCP/Oracle rates composed from separate
// rate SKUs) is badged distinctly so a glance tells checkable apart from
// composed (TASKS R4).
function DerivedBadge({ formula }: { formula?: string }) {
  return (
    <span
      className="text-derived bg-derived/15 ml-1.5 cursor-help rounded px-1.5 py-0.5 text-[10px] font-medium"
      title={
        formula
          ? `agent-derived: composed from cited rate SKUs (${formula})`
          : "agent-derived: composed from cited rate SKUs"
      }
    >
      derived
    </span>
  );
}

function ResultRow({
  row,
  rank,
  canonicalRegion,
  expanded,
  onToggle,
}: {
  row: CompareRowType;
  rank: number;
  canonicalRegion?: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const composite = row.citation?.composite ?? [];
  const isSynth = Boolean(row.synthesized) || composite.length > 0;
  // A row is expandable ("more details") only when it has a composite rate
  // breakdown to reveal. Default collapsed; the whole row is the toggle.
  const expandable = composite.length > 0;

  const native = row.region_native;
  const showCanon = Boolean(
    canonicalRegion && native && native !== canonicalRegion,
  );

  return (
    <>
      <tr
        className={cn(
          "border-t align-middle",
          expandable && "hover:bg-muted/40 cursor-pointer",
        )}
        onClick={expandable ? onToggle : undefined}
        aria-expanded={expandable ? expanded : undefined}
      >
        <td className="px-3 py-2 whitespace-nowrap">
          {expandable ? (
            <ChevronRightIcon
              className={cn(
                "text-muted-foreground mr-1 inline size-3.5 align-middle transition-transform",
                expanded && "rotate-90",
              )}
            />
          ) : (
            <span className="mr-1 inline-block size-3.5 align-middle" />
          )}
          <RankCell rank={rank} />
        </td>
        <td className="px-3 py-2 font-medium">{providerLabel(row.provider)}</td>
        <td className="px-3 py-2">
          <span className="font-mono">{row.instance_type}</span>
          {isSynth && <DerivedBadge formula={row.citation?.synthesis?.formula} />}
        </td>
        <td className="px-3 py-2">
          {/* provider-native region code; the canonical bucket it maps to is
              shown as a parenthetical only when it actually differs. */}
          <span title={showCanon ? `canonical: ${canonicalRegion}` : undefined}>
            {native ?? canonicalRegion ?? "-"}
          </span>
          {showCanon && (
            <span className="text-muted-foreground ml-1 text-[10px]">
              ({canonicalRegion})
            </span>
          )}
        </td>
        <td className="px-3 py-2 text-right">{row.vcpu_actual ?? "-"}</td>
        <td className="px-3 py-2 text-right">{gb(row.ram_gb_actual)}</td>
        <td className="px-3 py-2 text-right font-mono">{usd(row.hourly_usd)}</td>
        <td className="px-3 py-2 text-right font-mono font-semibold">
          {usd(row.monthly_usd)}
        </td>
        <td className="px-3 py-2">
          <CitationAge citation={row.citation} />
        </td>
        {/* interactive cell: clicks here verify the citation, they do not
            toggle the row. */}
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2">
            <CitationSource citation={row.citation} />
            <CitationExcerpt citation={row.citation} />
          </div>
        </td>
      </tr>

      {/* Composite sub-rows: each cited rate SKU that composed the synthesized
          price, independently verifiable (ADR-0007). Hidden until expanded. */}
      {expandable &&
        expanded &&
        composite.map((entry, j) => (
          <tr
            key={`${rowKey(row)}-sub-${j}`}
            className="bg-muted/30 text-muted-foreground align-middle text-xs"
          >
            <td className="px-3 py-1.5" />
            <td className="px-3 py-1.5" />
            <td className="px-3 py-1.5 pl-9" colSpan={5}>
              ↳ {entry.rate_unit ?? "rate"}
              {typeof entry.rate === "number" ? ` @ $${entry.rate}` : ""}
              {typeof entry.quantity === "number" ? ` × ${entry.quantity}` : ""}
            </td>
            <td className="px-3 py-1.5 text-right font-mono">
              {typeof entry.contribution_usd === "number"
                ? `$${entry.contribution_usd.toFixed(2)}`
                : "-"}
            </td>
            <td className="px-3 py-1.5" />
            <td className="px-3 py-1.5">
              <CitationExcerpt
                citation={{
                  json_path: entry.json_path,
                  source_url: entry.source_url,
                  snapshot: entry.snapshot,
                  age_hours: entry.age_hours,
                }}
              />
            </td>
          </tr>
        ))}
    </>
  );
}

export function ComparisonTableView({
  args,
  data,
}: {
  args: CompareArgs;
  data: CompareResult;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const rows = data.results ?? [];
  const unmet = data.unmet_requirements ?? [];
  const stale = data.data_quality?.overall_status === "stale";
  const specBits = [
    data.request?.vcpu ?? args.vcpu,
    data.request?.ram_gb ?? args.ram_gb,
  ];
  const heading = `Cheapest ${specBits[0] ?? "?"} vCPU · ${gb(specBits[1])}${
    data.request?.family && data.request.family !== "any"
      ? ` · ${data.request.family}`
      : ""
  }${data.request?.region ? ` in ${data.request.region}` : ""}`;

  return (
    <div className="aui-compare-root bg-card w-full rounded-lg border text-sm">
      <div className="aui-compare-header flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <span className="font-semibold">{heading}</span>
        {stale && (
          <span className="bg-stale/10 text-stale rounded px-2 py-0.5 text-xs font-medium">
            stale data
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <div className="text-muted-foreground px-4 py-3">
          No provider had a matching instance for this spec.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="aui-compare-table w-full border-collapse">
            <thead>
              <tr className="text-muted-foreground text-left text-xs">
                <th className="px-3 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Provider</th>
                <th className="px-3 py-2 font-medium">Instance</th>
                <th className="px-3 py-2 font-medium">Region</th>
                <th className="px-3 py-2 text-right font-medium">vCPU</th>
                <th className="px-3 py-2 text-right font-medium">RAM</th>
                <th className="px-3 py-2 text-right font-medium">Hourly</th>
                <th className="px-3 py-2 text-right font-medium">Monthly</th>
                <th className="px-3 py-2 font-medium">Snapshot</th>
                <th className="px-3 py-2 font-medium">Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const key = rowKey(row);
                return (
                  <ResultRow
                    key={`${key}-${i}`}
                    row={row}
                    rank={i + 1}
                    canonicalRegion={data.request?.region}
                    expanded={expanded.has(key)}
                    onToggle={() => toggle(key)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {unmet.length > 0 && (
        <div className="text-muted-foreground border-t px-4 py-2 text-xs">
          No match: {unmet.map((u) => providerLabel(u.provider)).join(", ")} had
          no instance meeting the spec.
        </div>
      )}

      {/* Equivalence basis (AGENTS.md): disclose what the cross-provider
          comparison holds on and, critically, what it does NOT normalize. */}
      {data.equivalence && (
        <div className="text-muted-foreground border-t px-4 py-2 text-xs">
          <span className="font-medium">Equivalence basis</span>
          {data.equivalence.family ? ` (${data.equivalence.family})` : ""}:
          matched {data.equivalence.dimensions_matched.join(", ") || "—"}. Not
          normalized:{" "}
          <span className="text-foreground/80">
            {data.equivalence.dimensions_not_normalized.join(", ") || "—"}
          </span>
          .
        </div>
      )}
    </div>
  );
}
