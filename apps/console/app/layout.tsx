import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Media OS · Creator studio",
  description: "Create distinctive AI influencers, develop sourced content and review every post in your creator workspace.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
