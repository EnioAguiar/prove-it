"use client";

import { useEffect, useState } from "react";
import { VERIFY_KEY, verifyReceipt, type VerifyResult } from "@/lib/verify";

export default function Verifier() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const handed = sessionStorage.getItem(VERIFY_KEY);
    if (handed) setText(handed);
  }, []);

  async function check() {
    setChecking(true);
    setError(null);
    setResult(null);
    try {
      setResult(await verifyReceipt(text));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Verification failed. Try again.");
    } finally {
      setChecking(false);
    }
  }

  async function loadFile(file: File | undefined) {
    if (!file) return;
    setText(await file.text());
    setResult(null);
  }

  return (
    <div className="page">
      <header className="masthead">
        <a className="wordmark wordmark-link" href="/">
          Prove It
        </a>
        <p className="masthead-note">
          Every receipt is signed with Ed25519 right after the run. Change one character and it
          stops verifying.
        </p>
        <a className="masthead-link" href="/api/public-key">
          Public key
        </a>
      </header>

      <main className="verify-layout">
        <section className="intake">
          <h1>Is this receipt real?</h1>
          <p className="lede">
            Paste a receipt or load <code>receipt.json</code>. Try editing the verdict first.
          </p>
          <div className="field">
            <div className="field-head">
              <label htmlFor="receipt">Receipt JSON</label>
              <label className="plain-button file-button">
                Load receipt.json
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(event) => loadFile(event.target.files?.[0])}
                />
              </label>
            </div>
            <textarea
              id="receipt"
              className="code-area"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setResult(null);
              }}
              spellCheck={false}
              wrap="off"
              rows={18}
              placeholder={'{\n  "verdict": "FAIL",\n  ...\n  "signature": "..."\n}'}
            />
          </div>
          <div className="submit-row">
            <button className="prove-button" type="button" onClick={check} disabled={checking || !text.trim()}>
              {checking ? "Checking…" : "Verify"}
            </button>
          </div>
        </section>

        <aside className="tape-column" aria-live="polite">
          <div className="tape">
            <div className="tape-head">
              <p className="tape-title">Prove It · verification</p>
              <p>Ed25519 · canonical JSON</p>
            </div>
            {!result && !error && (
              <p className="tape-empty">Nothing checked yet. Paste a receipt, then press Verify.</p>
            )}
            {error && (
              <p className="tape-error" role="alert">
                {error}
              </p>
            )}
            {result && (
              <section className="verdict" aria-label={result.valid ? "Genuine receipt" : "Forged receipt"}>
                <p className="verdict-label">This receipt</p>
                <p className={`stamp ${result.valid ? "stamp-pass" : "stamp-fail"}`}>
                  {result.valid ? "GENUINE" : "FORGED"}
                </p>
                <p className="verdict-meaning">{result.reason}</p>
              </section>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
