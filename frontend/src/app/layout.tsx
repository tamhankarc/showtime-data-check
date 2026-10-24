import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Provider Data Check",
  description: "Private provider catalog and fresh showtime checking.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
