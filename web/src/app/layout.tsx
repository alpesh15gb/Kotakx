import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kotak Neo Pro Terminal | Scalper & Options",
  description: "Sahi-inspired high-speed trading terminal powered by Kotak Neo API v3",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-obsidian text-zinc-100 antialiased">{children}</body>
    </html>
  );
}
