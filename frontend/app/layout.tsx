import type { Metadata } from "next";
import { Archivo, Big_Shoulders_Stencil, Fragment_Mono } from "next/font/google";
import "./globals.css";

const body = Archivo({
  variable: "--font-body",
  subsets: ["latin"],
  axes: ["wdth"],
});

const stencil = Big_Shoulders_Stencil({
  variable: "--font-stencil",
  subsets: ["latin"],
  weight: ["800"],
});

const mono = Fragment_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  weight: ["400"],
});

export const metadata: Metadata = {
  title: "Prove It: run the code before you trust it",
  description:
    "Paste Python. Prove It writes property tests, runs them in a sealed container on a Vultr VM, and lets pytest's exit code decide the verdict.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${body.variable} ${stencil.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
