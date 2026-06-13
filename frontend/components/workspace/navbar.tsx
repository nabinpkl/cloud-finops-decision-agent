"use client";

import { Sparkles } from "lucide-react";

import { ThemeSwitcher } from "@/components/theme/theme-switcher";
import { cn } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace-store";

const NAV_LINKS = ["Compare", "Snapshots", "Providers", "Docs"];

// Full-width sticky top bar. It lives OUTSIDE the AG-UI runtime provider, so it
// talks to the agent panel only through the workspace store (the Ask AI toggle).
// It never shifts when the panel opens; the panel docks BELOW it (top-[--top-h]).
export function Navbar() {
  const agentOpen = useWorkspace((s) => s.agentOpen);
  const toggleAgent = useWorkspace((s) => s.toggleAgent);

  return (
    <header className="bg-card/80 sticky top-0 z-50 flex h-[var(--top-h)] items-center gap-6 border-b px-5 backdrop-blur-md">
      <div className="flex items-center gap-2 font-semibold tracking-tight">
        <span className="from-brand size-[22px] rounded-md bg-gradient-to-br to-fuchsia-500" />
        cloudprice
        <span className="text-muted-foreground font-normal">/compare</span>
      </div>

      <nav className="hidden items-center gap-1 md:flex">
        {NAV_LINKS.map((label, i) => (
          <a
            key={label}
            href="#"
            className={cn(
              "rounded-md px-2.5 py-1.5 text-[13.5px] font-medium",
              i === 0
                ? "bg-muted text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {label}
          </a>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3">
        <div className="text-muted-foreground hidden h-[34px] items-center gap-2 rounded-lg border bg-background px-3 text-[13px] lg:flex">
          <span aria-hidden>⌕</span>
          <span>Search specs, providers…</span>
          <kbd className="ml-2 rounded border px-1.5 text-[11px]">⌘K</kbd>
        </div>

        <button
          type="button"
          onClick={toggleAgent}
          aria-pressed={agentOpen}
          className={cn(
            "inline-flex h-[34px] items-center gap-1.5 rounded-lg border px-3 text-[13px] font-semibold transition",
            agentOpen
              ? "bg-brand text-brand-foreground border-transparent"
              : "border-brand/35 bg-brand/10 text-brand hover:bg-brand/15",
          )}
        >
          <Sparkles className="size-4" />
          Ask AI
        </button>

        <ThemeSwitcher />

        <span className="from-brand inline-flex size-[30px] items-center justify-center rounded-full bg-gradient-to-br to-sky-500 text-xs font-semibold text-white">
          NP
        </span>
      </div>
    </header>
  );
}
