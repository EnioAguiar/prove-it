"use client";

import type { KeyboardEvent } from "react";
import type { Example } from "@/lib/types";

const MAX_BYTES = 20_000;
const encoder = new TextEncoder();

interface Props {
  code: string;
  intent: string;
  examples: Example[];
  running: boolean;
  onCode: (code: string) => void;
  onIntent: (intent: string) => void;
  onSubmit: () => void;
}

export default function CodeInput({
  code,
  intent,
  examples,
  running,
  onCode,
  onIntent,
  onSubmit,
}: Props) {
  const bytes = encoder.encode(code).length;
  const tooLarge = bytes > MAX_BYTES;
  const empty = !code.trim();

  function onCodeKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      onSubmit();
      return;
    }
    // Tab indents like an editor; Shift+Tab still moves focus out.
    if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      const area = event.currentTarget;
      const { selectionStart: from, selectionEnd: to } = area;
      onCode(code.slice(0, from) + "    " + code.slice(to));
      requestAnimationFrame(() => area.setSelectionRange(from + 4, from + 4));
    }
  }

  return (
    <form
      className="intake-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="examples" role="group" aria-labelledby="examples-label">
        <span id="examples-label" className="examples-label">
          Load an example
        </span>
        {examples.map((example) => (
          <button
            key={example.id}
            type="button"
            className="example-button"
            disabled={running}
            onClick={() => {
              onCode(example.code);
              onIntent(example.intent);
            }}
          >
            {example.label}
          </button>
        ))}
      </div>

      <div className="field">
        <div className="field-head">
          <label htmlFor="code">Python code</label>
          <span className={tooLarge ? "byte-count over" : "byte-count"}>
            {bytes.toLocaleString("en-US")} / {MAX_BYTES.toLocaleString("en-US")} bytes
          </span>
        </div>
        <textarea
          id="code"
          className="code-area"
          wrap="off"
          value={code}
          onChange={(event) => onCode(event.target.value)}
          onKeyDown={onCodeKey}
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          rows={16}
          placeholder={"def add(a, b):\n    return a + b"}
          aria-describedby="code-help"
        />
        <p id="code-help" className="field-help">
          Standard library, numpy and pandas are installed. Nothing else, and no internet.
        </p>
      </div>

      <div className="field">
        <label htmlFor="intent">What should this code do? (optional)</label>
        <input
          id="intent"
          className="intent-input"
          value={intent}
          onChange={(event) => onIntent(event.target.value)}
          placeholder="Installments must add up exactly to the total."
          autoComplete="off"
        />
      </div>

      <div className="submit-row">
        <button className="prove-button" type="submit" disabled={running || empty || tooLarge}>
          {running ? "Running…" : "Prove it"}
        </button>
        <span className="submit-hint">
          {tooLarge ? "Cut the code below 20,000 bytes to run it." : "Ctrl + Enter also runs it."}
        </span>
      </div>
    </form>
  );
}
