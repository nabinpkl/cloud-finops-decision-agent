import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Cloud FinOps pricing agent",
  description:
    "Citation-backed cloud pricing: cheapest VM across providers for a spec, with verifiable sources.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">
        <div className="flex h-screen flex-col">
          <header className="bg-background border-b px-4 py-2">
            <h1 className="text-lg font-semibold">
              Cloud FinOps pricing agent
            </h1>
          </header>
          <main className="flex-1 overflow-hidden">{children}</main>
        </div>
      </body>
    </html>
  );
}
