import type { Metadata } from "next";
import { Navbar } from "@/components/workspace/navbar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cloud FinOps pricing agent",
  description:
    "Citation-backed cloud pricing: cheapest VM across providers for a spec, with verifiable sources.",
};

const themeInitScript = `
(() => {
  try {
    const themeKey = "cloud-finops-theme";
    const brightnessKey = "cloud-finops-brightness";
    const validThemes = new Set(["system", "light", "dark"]);
    const validBrightness = new Set(["dim", "standard", "bright"]);
    const root = document.documentElement;
    const storedTheme = localStorage.getItem(themeKey);
    const storedBrightness = localStorage.getItem(brightnessKey);
    const preference = validThemes.has(storedTheme)
      ? storedTheme
      : "system";
    const theme = preference === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : preference;
    const brightness = validBrightness.has(storedBrightness)
      ? storedBrightness
      : "standard";

    root.dataset.themePreference = preference;
    root.dataset.theme = theme;
    root.dataset.brightness = brightness;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
  } catch {
  }
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      data-theme-preference="system"
      data-theme="light"
      data-brightness="standard"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-sans antialiased">
        <div className="flex h-screen flex-col">
          <Navbar />
          <main className="flex-1 overflow-hidden">{children}</main>
        </div>
      </body>
    </html>
  );
}
