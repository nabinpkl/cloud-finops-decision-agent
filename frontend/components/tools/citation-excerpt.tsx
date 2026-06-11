"use client";

import { FileSearchIcon, LoaderIcon } from "lucide-react";
import { useState } from "react";

import type { Citation } from "@/components/tools/compare-result-types";
import { cn } from "@/lib/utils";

// Excerpt-on-click verification hunk (ADR-0008, TASKS R8). The Citation carries
// a logical snapshot ref {provider, snapshot_iso, filename} plus a json_path;
// the backend /citation/excerpt endpoint resolves it to the file on disk and
// returns the cited value rendered in context with line numbers. The browser
// only ever hits the same-origin /citation path (next.config.js rewrite); it
// never holds a backend URL or a filesystem path.

type ExcerptLine = { n: number; text: string; match?: boolean };
type Excerpt = {
  json_path: string;
  matched_value?: string;
  match_line?: number;
  lines: ExcerptLine[];
  error?: string;
};

function snapshotRef(citation?: Citation) {
  return citation?.snapshot;
}

export function CitationExcerpt({ citation }: { citation?: Citation }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [excerpt, setExcerpt] = useState<Excerpt | null>(null);
  const [error, setError] = useState<string | null>(null);

  const snapshot = snapshotRef(citation);
  const jsonPath = citation?.json_path;
  if (!snapshot || !jsonPath) return null;
  const ref = snapshot;
  const path = jsonPath;

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    if (excerpt || loading) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        provider: ref.provider,
        snapshot_iso: ref.snapshot_iso,
        filename: ref.filename,
        path,
      });
      const res = await fetch(`/citation/excerpt?${params.toString()}`);
      if (!res.ok) {
        setError(`could not load excerpt (${res.status})`);
        return;
      }
      setExcerpt((await res.json()) as Excerpt);
    } catch {
      setError("could not load excerpt");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="aui-citation-excerpt">
      <button
        type="button"
        onClick={toggle}
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
        title="Show the cited value in its snapshot context"
        aria-expanded={open}
      >
        <FileSearchIcon className="size-3.5" />
      </button>
      {open && (
        <div className="bg-popover mt-2 rounded-md border p-2 text-xs">
          {loading && (
            <span className="text-muted-foreground inline-flex items-center gap-1">
              <LoaderIcon className="size-3.5 animate-spin" /> loading excerpt...
            </span>
          )}
          {error && <span className="text-destructive">{error}</span>}
          {excerpt?.error && (
            <span className="text-destructive">{excerpt.error}</span>
          )}
          {excerpt && !excerpt.error && (
            <>
              <div className="text-muted-foreground mb-1 font-mono break-all">
                {excerpt.json_path}
              </div>
              <pre className="bg-muted overflow-x-auto rounded p-2 font-mono leading-relaxed">
                {excerpt.lines.map((line) => (
                  <div
                    key={line.n}
                    className={cn(
                      "whitespace-pre",
                      line.match && "bg-primary/10 text-foreground font-semibold",
                    )}
                  >
                    <span className="text-muted-foreground mr-3 inline-block w-8 text-right select-none">
                      {line.n}
                    </span>
                    {line.text}
                  </div>
                ))}
              </pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
