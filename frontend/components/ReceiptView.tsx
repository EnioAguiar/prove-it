"use client";

import { useState } from "react";
import type { Receipt } from "@/lib/types";

function short(hash: string): string {
  const value = hash.includes(":") ? hash.split(":").pop()! : hash;
  return value.length > 16 ? `${value.slice(0, 12)}…${value.slice(-4)}` : value;
}

export default function ReceiptView({ receipt }: { receipt: Receipt }) {
  const [copied, setCopied] = useState<"idle" | "done" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(receipt.command);
      setCopied("done");
    } catch {
      setCopied("failed");
    }
  }

  const rows: [string, string][] = [
    ["code sha256", short(receipt.code_sha256)],
    ["tests sha256", short(receipt.tests_sha256)],
    ["image", receipt.image_digest ? short(receipt.image_digest) : receipt.image],
    ["runtime", receipt.runtime],
    ...Object.entries(receipt.limits).map(([key, value]): [string, string] => [key.replaceAll("_", " "), value]),
    ["exit code", String(receipt.exit_code)],
    ["duration", `${receipt.duration_s.toFixed(2)} s`],
  ];

  return (
    <section className="receipt" aria-labelledby="receipt-title">
      <h2 id="receipt-title" className="receipt-title">
        Receipt
      </h2>
      <dl className="receipt-rows">
        {rows.map(([name, value]) => (
          <div key={name} className="receipt-row">
            <dt>{name}</dt>
            <dd title={value}>{value}</dd>
          </div>
        ))}
      </dl>
      <details className="receipt-command">
        <summary>Rerun it yourself</summary>
        <pre>{receipt.command}</pre>
      </details>
      <button type="button" className="tape-button" onClick={copy}>
        {copied === "done" ? "Command copied" : "Copy rerun command"}
      </button>
      {copied === "failed" && (
        <p className="tape-note">The browser blocked the clipboard. Open “Rerun it yourself” and copy it by hand.</p>
      )}
    </section>
  );
}
