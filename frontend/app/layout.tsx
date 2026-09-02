import type { Metadata } from "next";
import { JetBrains_Mono, Rajdhani } from "next/font/google";
import { AuthGate } from "@/components/auth/AuthGate";
import { PRODUCT_NAME, TAGLINE } from "@/lib/branding";
import "./globals.css";

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-mono",
  display: "swap",
});

const rajdhani = Rajdhani({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-display",
  display: "swap",
});

export const metadata: Metadata = {
  title: `${PRODUCT_NAME} — ${TAGLINE}`,
  description:
    "AI-Enabled Real-Time Digital Twin for Health Monitoring, Fault Prediction and Mission Reliability Enhancement of Aero Piston Engines used in MALE UAVs.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${jetbrainsMono.variable} ${rajdhani.variable}`}>
      <body className="font-sans antialiased">
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
