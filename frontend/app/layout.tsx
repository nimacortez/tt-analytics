import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "TT Analytics",
  description: "Table tennis match models and stats",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">TT Analytics</Link>
          <span className="muted">mock data · every number is as of kickoff</span>
        </header>
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
