import type { Metadata, Viewport } from "next";
import { Anek_Latin, Mukta } from "next/font/google";
import { AuthGate } from "@/components/auth/AuthGate";
import { returningScript } from "@/lib/returning";
import { strings } from "@/lib/strings";
import "./globals.css";

const display = Anek_Latin({
  subsets: ["latin"],
  axes: ["wdth"],
  variable: "--font-anek",
  display: "swap",
});

const body = Mukta({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mukta",
  display: "swap",
});

export const metadata: Metadata = {
  title: strings.meta.title,
  description: strings.meta.description,
  applicationName: strings.appName,
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#f4f5fa",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang={strings.locale} className={`${display.variable} ${body.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: returningScript }} />
      </head>
      <body className="antialiased">
        <AuthGate>{children}</AuthGate>
      </body>
    </html>
  );
}
