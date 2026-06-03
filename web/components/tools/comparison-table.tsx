"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";
import { ExternalLinkIcon, LoaderIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// Mirrors the backend `compare` tool result (api/tools_core.run_compare ->
// api/wire.wire_response). A citation is either atomic (age_hours/source_url at
// the top) or synthesized (Oracle-style: a formula plus a `composite` list,
// each constituent carrying its own age/source). The frontend reads whichever
// is present; the snapshot age and source link per row are the citation
// contract surfaced visually (AGENTS.md).
type Constituent = {
  source_url?: string;
  age_hours?: number;
  rate_unit?: string;
};
type Citation = {
  age_hours?: number;
  source_url?: string;
  json_path?: string;
  fetched_at?: string;
  snapshot?: { provider: string; snapshot_iso: string; filename: string };
  synthesis?: { rule: string; formula: string };
  composite?: Constituent[];
};
type CompareRow = {
  provider: string;
  instance_type: string;
  region_native?: string;
  vcpu_actual?: number;
  ram_gb_actual?: number;
  hourly_usd?: number;
  monthly_usd?: number;
  synthesized?: boolean;
  citation?: Citation;
};
type CompareResult = {
  request?: { vcpu?: number; ram_gb?: number; region?: string; family?: string };
  results?: CompareRow[];
  ranked_by?: string;
  unmet_requirements?: Array<{ provider: string; reason: string }>;
  data_quality?: { overall_status?: string };
};
type CompareArgs = {
  vcpu?: number;
  ram_gb?: number;
  region?: string;
  family?: string;
  providers?: string[];
  expand?: string;
};

const STALE_HOURS = 24;
const MEDALS = ["🥇", "🥈", "🥉"];
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

function rowAgeHours(citation?: Citation): number | undefined {
  if (!citation) return undefined;
  if (typeof citation.age_hours === "number") return citation.age_hours;
  const ages = (citation.composite ?? [])
    .map((c) => c.age_hours)
    .filter((a): a is number => typeof a === "number");
  return ages.length ? Math.max(...ages) : undefined;
}

function rowSource(citation?: Citation): string | undefined {
  return citation?.source_url ?? citation?.composite?.[0]?.source_url;
}

function usd(n?: number): string {
  return typeof n === "number" ? `$${n.toFixed(2)}` : "—";
}

function gb(n?: number): string {
  return typeof n === "number" ? `${Number.isInteger(n) ? n : n.toFixed(1)} GB` : "—";
}

function AgeChip({ ageHours }: { ageHours?: number }) {
  if (typeof ageHours !== "number") return null;
  const stale = ageHours >= STALE_HOURS;
  const label = ageHours < 1 ? "just fetched" : `${Math.round(ageHours)}h old`;
  return (
    <span
      className={cn(
        "aui-compare-age inline-block rounded px-1.5 py-0.5 text-xs whitespace-nowrap",
        stale
          ? "bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground",
      )}
      title={`snapshot ${label}${stale ? " — past the 24h freshness threshold" : ""}`}
    >
      {label}
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
      <span>Comparing providers{spec ? ` for ${spec}` : ""}…</span>
    </div>
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
                const source = rowSource(row.citation);
                return (
                  <tr
                    key={`${row.provider}-${row.instance_type}-${i}`}
                    className="border-t align-top"
                  >
                    <td className="px-3 py-2">{MEDALS[i] ?? i + 1}</td>
                    <td className="px-3 py-2 font-medium">
                      {providerLabel(row.provider)}
                    </td>
                    <td className="px-3 py-2">
                      {row.instance_type}
                      {row.synthesized && row.citation?.synthesis?.formula && (
                        <span
                          className="text-muted-foreground ml-1 cursor-help"
                          title={`synthesized: ${row.citation.synthesis.formula}`}
                        >
                          (synth)
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">{row.vcpu_actual ?? "—"}</td>
                    <td className="px-3 py-2 text-right">{gb(row.ram_gb_actual)}</td>
                    <td className="px-3 py-2 text-right">{usd(row.hourly_usd)}</td>
                    <td className="px-3 py-2 text-right font-semibold">
                      {usd(row.monthly_usd)}
                    </td>
                    <td className="px-3 py-2">
                      <AgeChip ageHours={rowAgeHours(row.citation)} />
                    </td>
                    <td className="px-3 py-2">
                      {source ? (
                        <a
                          href={source}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
                          title={source}
                        >
                          <ExternalLinkIcon className="size-3.5" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                  </tr>
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
