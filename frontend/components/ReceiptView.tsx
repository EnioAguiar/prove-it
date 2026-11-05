"use client";

import { useState } from "react";
import type { Receipt } from "@/lib/types";
import { VERIFY_KEY } from "@/lib/verify";

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
    ["verdict", receipt.verdict],
    ["signed by", `key ${receipt.key_id}`],
    ["signature", short(receipt.signature)],
  ];

  function openVerifier() {
    sessionStorage.setItem(VERIFY_KEY, JSON.stringify(receipt, null, 2));
    window.location.href = "/verify/";
  }

  function downloadJson() {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(receipt, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = "receipt.json";
    link.click();
    URL.revokeObjectURL(url);
  }

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
      <div className="receipt-actions">
        <button type="button" className="tape-button" onClick={openVerifier}>
          Verify this receipt
        </button>
        <button type="button" className="tape-button" onClick={downloadJson}>
          Download receipt.json
        </button>
      </div>
    </section>
  );
}
