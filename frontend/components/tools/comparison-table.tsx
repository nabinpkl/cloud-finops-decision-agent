"use client";

import { makeAssistantToolUI } from "@assistant-ui/react";

import {
  ComparisonSkeleton,
  ComparisonTableView,
} from "@/components/tools/comparison-table-view";
import type {
  CompareArgs,
  CompareResult,
} from "@/components/tools/compare-result-types";

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
    return <ComparisonSkeleton args={args} />;
  }
  return <ComparisonTableView args={args} data={data} />;
};

// makeAssistantToolUI registers this renderer for `compare` tool-call parts.
// Mounted once in the provider tree, it replaces the JSON ToolFallback inside
// the agent panel's thread with the same ComparisonTableView the manual
// dashboard renders directly.
export const ComparisonTable = makeAssistantToolUI<CompareArgs, CompareResult>({
  toolName: "compare",
  render: ComparisonTableImpl,
});
