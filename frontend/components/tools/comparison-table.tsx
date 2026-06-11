"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { LoaderIcon } from "lucide-react";

import { CitationAge, CitationSource } from "@/components/tools/citation-cells";
import { CitationExcerpt } from "@/components/tools/citation-excerpt";
import type {
  CompareArgs,
  CompareResult,
} from "@/components/tools/compare-result-types";

const ACRONYMS = new Set(["aws", "gcp", "ibm"]);

// Provider ids are lowercase (aws, gcp, vultr). Render acronyms uppercase and
// the rest title-cased, so "aws" -> "AWS" and "vultr" -> "Vultr".
function providerLabel(provider: string): string {
  const lower = provider.toLowerCase();
  if (ACRONYMS.has(lower)) return lower.toUpperCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function asResult(result: unknown): CompareResult | null {
  if (!result) return null;
  // The OpenAI-agents adapter sends the dict directly; the langchain adapter
  // sends the artifact dict. Guard the string case defensively.
  if (typeof result === "string") {
    try {
      return JSON.parse(result) as CompareResult;
    } catch {
      return null;
    }
  }
  if (typeof result === "object") return result as CompareResult;
  return null;
}

function usd(n?: number): string {
  return typeof n === "number" ? `$${n.toFixed(2)}` : "-";
}

function gb(n?: number): string {
  return typeof n === "number"
    ? `${Number.isInteger(n) ? n : n.toFixed(1)} GB`
    : "-";
}

function RankCell({ rank }: { rank: number }) {
  return (
    <span className="bg-muted text-muted-foreground inline-flex size-6 items-center justify-center rounded text-xs font-medium">
      {rank}
    </span>
  );
}

function Skeleton({ args }: { args: CompareArgs }) {
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

// Two visual trust tiers (TASKS R4). The verified tier is a plain cell whose
// number is checkable through the Source primitive + excerpt-on-click. The
// agent-derived tier (synthesized GCP/Oracle rates composed from separate rate
// SKUs) is badged distinctly so a glance tells checkable apart from composed.
function DerivedBadge({ formula }: { formula?: string }) {
  return (
    <span
      className="bg-amber-500/10 text-amber-700 dark:text-amber-400 ml-1.5 cursor-help rounded px-1.5 py-0.5 text-[10px] font-medium"
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

type CompareRowType = NonNullable<CompareResult["results"]>[number];

function ResultRows({
  row,
  rank,
  isSynth,
  canonicalRegion,
}: {
  row: CompareRowType;
  rank: number;
  isSynth: boolean;
  canonicalRegion?: string;
}) {
  const composite = row.citation?.composite ?? [];
  return (
    <>
      <tr className="border-t align-top">
        <td className="px-3 py-2">
          <RankCell rank={rank} />
        </td>
        <td className="px-3 py-2 font-medium">{providerLabel(row.provider)}</td>
        <td className="px-3 py-2">
          {row.instance_type}
          {isSynth && <DerivedBadge formula={row.citation?.synthesis?.formula} />}
        </td>
        <td className="px-3 py-2">
          {/* Region: provider-native code, with the canonical bucket it maps to
              as the title so both halves of the equivalence are visible. */}
          <span title={canonicalRegion ? `canonical: ${canonicalRegion}` : undefined}>
            {row.region_native ?? "-"}
          </span>
          {canonicalRegion && (
            <span className="text-muted-foreground ml-1 text-[10px]">
              ({canonicalRegion})
            </span>
          )}
        </td>
        <td className="px-3 py-2 text-right">{row.vcpu_actual ?? "-"}</td>
        <td className="px-3 py-2 text-right">{gb(row.ram_gb_actual)}</td>
        <td className="px-3 py-2 text-right">{usd(row.hourly_usd)}</td>
        <td className="px-3 py-2 text-right font-semibold">
          {usd(row.monthly_usd)}
        </td>
        <td className="px-3 py-2">
          <CitationAge citation={row.citation} />
        </td>
        <td className="px-3 py-2">
          <div className="flex items-center gap-2">
            <CitationSource citation={row.citation} />
            <CitationExcerpt citation={row.citation} />
          </div>
        </td>
      </tr>
      {/* Composite sub-rows: each cited rate SKU that composed the synthesized
          price, independently verifiable (ADR-0007, TASKS R8). */}
      {composite.map((entry, j) => (
        <tr
          key={`${row.provider}-${row.instance_type}-sub-${j}`}
          className="bg-muted/30 text-muted-foreground align-top text-xs"
        >
          <td className="px-3 py-1.5" />
          <td className="px-3 py-1.5" />
          <td className="px-3 py-1.5 pl-6" colSpan={5}>
            ↳ {entry.rate_unit ?? "rate"}
            {typeof entry.rate === "number" ? ` @ $${entry.rate}` : ""}
            {typeof entry.quantity === "number" ? ` × ${entry.quantity}` : ""}
          </td>
          <td className="px-3 py-1.5 text-right">
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

const ComparisonTableImpl = ({
  args,
  result,
  status,
}: {
  args: CompareArgs;
  result?: unknown;
  status: { readonly type: string };
}) => {
  const data = asResult(result);
  if (status.type === "running" || !data) {
    return <Skeleton args={args} />;
  }

  const rows = data.results ?? [];
  const unmet = data.unmet_requirements ?? [];
  const stale = data.data_quality?.overall_status === "stale";
  const specBits = [
    data.request?.vcpu ?? args.vcpu,
    data.request?.ram_gb ?? args.ram_gb,
  ];
  const heading = `Cheapest ${specBits[0] ?? "?"} vCPU · ${gb(
    specBits[1],
  )}${data.request?.family && data.request.family !== "any" ? ` · ${data.request.family}` : ""}${
    data.request?.region ? ` in ${data.request.region}` : ""
  }`;

  return (
    <div className="aui-compare-root w-full rounded-lg border text-sm">
      <div className="aui-compare-header flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <span className="font-semibold">{heading}</span>
        {stale && (
          <span className="bg-destructive/10 text-destructive rounded px-2 py-0.5 text-xs font-medium">
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
                const composite = row.citation?.composite ?? [];
                const isSynth = Boolean(row.synthesized) || composite.length > 0;
                return (
                  <ResultRows
                    key={`${row.provider}-${row.instance_type}-${i}`}
                    row={row}
                    rank={i + 1}
                    isSynth={isSynth}
                    canonicalRegion={data.request?.region}
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

      {/* Equivalence basis (AGENTS.md): when a family is requested, disclose what
          the cross-provider comparison holds on and, critically, what it does
          NOT normalize, so the equivalence is never silently asserted. */}
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
};

// makeAssistantToolUI registers this renderer for tool-call parts whose
// toolName is "compare". Mounted once in the provider tree (see app/page.tsx),
// it makes `part.toolUI` resolve in thread.tsx, replacing the JSON ToolFallback.
export const ComparisonTable = makeAssistantToolUI<CompareArgs, CompareResult>({
  toolName: "compare",
  render: ComparisonTableImpl,
});
