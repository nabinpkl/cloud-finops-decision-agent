"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  CheckIcon,
  ChevronDownIcon,
  MonitorIcon,
  MoonIcon,
  SunDimIcon,
  SunIcon,
  SunMediumIcon,
} from "lucide-react";
import { DropdownMenu } from "radix-ui";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const THEME_KEY = "cloud-finops-theme";
const BRIGHTNESS_KEY = "cloud-finops-brightness";
const THEME_VALUES = ["system", "light", "dark"] as const;
const BRIGHTNESS_VALUES = ["dim", "standard", "bright"] as const;

type ThemePreference = (typeof THEME_VALUES)[number];
type ThemeMode = "light" | "dark";
type BrightnessMode = (typeof BRIGHTNESS_VALUES)[number];

const isThemePreference = (value: string | null): value is ThemePreference =>
  value === "system" || value === "light" || value === "dark";

const isBrightnessMode = (value: string | null): value is BrightnessMode =>
  value === "dim" || value === "standard" || value === "bright";

const getSystemTheme = (): ThemeMode => {
  if (typeof window === "undefined") {
    return "light";
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};

const resolveTheme = (preference: ThemePreference): ThemeMode =>
  preference === "system" ? getSystemTheme() : preference;

const getPreferredTheme = (): ThemePreference => {
  if (typeof window === "undefined") {
    return "system";
  }

  const stored = window.localStorage.getItem(THEME_KEY);
  return isThemePreference(stored) ? stored : "system";
};

const getStoredBrightness = (): BrightnessMode => {
  if (typeof window === "undefined") {
    return "standard";
  }

  const stored = window.localStorage.getItem(BRIGHTNESS_KEY);
  return isBrightnessMode(stored) ? stored : "standard";
};

const applyTheme = (
  preference: ThemePreference,
  theme: ThemeMode,
  brightness: BrightnessMode,
) => {
  const root = document.documentElement;

  root.dataset.themePreference = preference;
  root.dataset.theme = theme;
  root.dataset.brightness = brightness;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
};

export function ThemeSwitcher() {
  const [themePreference, setThemePreference] =
    useState<ThemePreference>("system");
  const [resolvedTheme, setResolvedTheme] = useState<ThemeMode>("light");
  const [brightness, setBrightness] = useState<BrightnessMode>("standard");

  useEffect(() => {
    const preferredTheme = getPreferredTheme();
    const activeTheme = resolveTheme(preferredTheme);
    const preferredBrightness = getStoredBrightness();

    setThemePreference(preferredTheme);
    setResolvedTheme(activeTheme);
    setBrightness(preferredBrightness);
    applyTheme(preferredTheme, activeTheme, preferredBrightness);
  }, []);

  useEffect(() => {
    if (themePreference !== "system") {
      return;
    }

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const activeTheme = resolveTheme("system");
      setResolvedTheme(activeTheme);
      applyTheme("system", activeTheme, brightness);
    };

    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [brightness, themePreference]);

  const updateTheme = (nextPreference: ThemePreference) => {
    const activeTheme = resolveTheme(nextPreference);

    setThemePreference(nextPreference);
    setResolvedTheme(activeTheme);
    window.localStorage.setItem(THEME_KEY, nextPreference);
    applyTheme(nextPreference, activeTheme, brightness);
  };

  const updateBrightness = (nextBrightness: BrightnessMode) => {
    setBrightness(nextBrightness);
    window.localStorage.setItem(BRIGHTNESS_KEY, nextBrightness);
    applyTheme(themePreference, resolvedTheme, nextBrightness);
  };

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="Theme settings"
          title="Theme settings"
          className="size-9 shrink-0 rounded-full"
        >
          <ActiveThemeIcon preference={themePreference} theme={resolvedTheme} />
          <ChevronDownIcon className="text-muted-foreground -ml-2 size-3" />
        </Button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={8}
          className="bg-popover text-popover-foreground z-50 w-56 rounded-md border p-1 shadow-md outline-none"
        >
          <DropdownMenu.Label className="text-muted-foreground px-2 py-1.5 text-xs font-medium">
            Theme
          </DropdownMenu.Label>
          <ThemeMenuItem
            active={themePreference === "system"}
            icon={<MonitorIcon className="size-4" />}
            label="System"
            onSelect={() => updateTheme("system")}
          />
          <ThemeMenuItem
            active={themePreference === "light"}
            icon={<SunIcon className="size-4" />}
            label="Light"
            onSelect={() => updateTheme("light")}
          />
          <ThemeMenuItem
            active={themePreference === "dark"}
            icon={<MoonIcon className="size-4" />}
            label="Dark"
            onSelect={() => updateTheme("dark")}
          />

          <DropdownMenu.Separator className="bg-border my-1 h-px" />
          <DropdownMenu.Label className="text-muted-foreground px-2 py-1.5 text-xs font-medium">
            Brightness
          </DropdownMenu.Label>
          <ThemeMenuItem
            active={brightness === "dim"}
            icon={<SunDimIcon className="size-4" />}
            label="Dim"
            onSelect={() => updateBrightness("dim")}
          />
          <ThemeMenuItem
            active={brightness === "standard"}
            icon={<SunMediumIcon className="size-4" />}
            label="Standard"
            onSelect={() => updateBrightness("standard")}
          />
          <ThemeMenuItem
            active={brightness === "bright"}
            icon={<SunIcon className="size-4" />}
            label="Bright"
            onSelect={() => updateBrightness("bright")}
          />
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function ActiveThemeIcon({
  preference,
  theme,
}: {
  preference: ThemePreference;
  theme: ThemeMode;
}) {
  if (preference === "system") {
    return <MonitorIcon className="size-4" />;
  }

  if (theme === "dark") {
    return <MoonIcon className="size-4" />;
  }

  return <SunIcon className="size-4" />;
}

function ThemeMenuItem({
  active,
  icon,
  label,
  onSelect,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  onSelect: () => void;
}) {
  return (
    <DropdownMenu.Item
      aria-checked={active}
      className={cn(
        "focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none select-none",
        active && "bg-accent text-accent-foreground",
      )}
      onSelect={onSelect}
    >
      {icon}
      <span className="flex-1">{label}</span>
      {active ? <CheckIcon className="size-4" /> : null}
    </DropdownMenu.Item>
  );
}
