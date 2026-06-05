"use client";

import { ExternalLinkIcon } from "lucide-react";

import type { Citation } from "@/components/tools/compare-result-types";
import { cn } from "@/lib/utils";

const STALE_HOURS = 24;

function citationAgeHours(citation?: Citation): number | undefined {
  if (!citation) return undefined;
  if (typeof citation.age_hours === "number") return citation.age_hours;
  const ages = (citation.composite ?? [])
    .map((constituent) => constituent.age_hours)
    .filter((age): age is number => typeof age === "number");
  return ages.length ? Math.max(...ages) : undefined;
}

function citationSource(citation?: Citation): string | undefined {
  return citation?.source_url ?? citation?.composite?.[0]?.source_url;
}

export function CitationAge({ citation }: { citation?: Citation }) {
  const ageHours = citationAgeHours(citation);
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
      title={`snapshot ${label}${stale ? " past the 24h freshness threshold" : ""}`}
    >
      {label}
    </span>
  );
}

export function CitationSource({ citation }: { citation?: Citation }) {
  const source = citationSource(citation);
  if (!source) {
    return <span className="text-muted-foreground">-</span>;
  }
  return (
    <a
      href={source}
      target="_blank"
      rel="noopener noreferrer"
      className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
      title={source}
    >
      <ExternalLinkIcon className="size-3.5" />
    </a>
  );
}
