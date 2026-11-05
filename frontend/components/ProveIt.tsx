"use client";

import { useRef, useState } from "react";
import CodeInput from "./CodeInput";
import Findings from "./Findings";
import Timeline, { type TapeLine } from "./Timeline";
import { ReviewError, streamReview } from "@/lib/review";
import type { Example, Report, StepEvent } from "@/lib/types";

export default function ProveIt({ examples }: { examples: Example[] }) {
  const [code, setCode] = useState(examples[0]?.code ?? "");
  const [intent, setIntent] = useState(examples[0]?.intent ?? "");
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<TapeLine[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const abort = useRef<AbortController | null>(null);

  function addStep(event: StepEvent, at: number) {
    setLines((current) => {
      const last = current.at(-1);
      // A step's result replaces its own "start" line, so each step prints once, stamped with
      // the time it finished.
      if (last && last.step === event.step && last.status === "start" && event.status !== "start") {
        return [...current.slice(0, -1), { ...event, at, key: last.key }];
      }
      return [...current, { ...event, at, key: current.length }];
    });
  }

  async function prove() {
    if (running || !code.trim()) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;
    const start = performance.now();
    setRunning(true);
    setLines([]);
    setReport(null);
    setError(null);
    setStartedAt(Date.now());
    try {
      await streamReview(
        code,
        intent,
        {
          onStep: (event) => addStep(event, (performance.now() - start) / 1000),
          onReport: setReport,
        },
        controller.signal,
      );
    } catch (caught) {
      if (controller.signal.aborted) return;
      setError(
        caught instanceof ReviewError
          ? caught.message
          : "Something broke while reading the result. Try again.",
      );
    } finally {
      if (abort.current === controller) setRunning(false);
    }
  }

  return (
    <div className="page">
      <header className="masthead">
        <span className="wordmark">Prove It</span>
        <p className="masthead-note">
          Runs on a Vultr VM. Verdicts come from pytest&apos;s exit code, never from the model.
        </p>
        <a className="masthead-link" href="/verify/">
          Verify a receipt
        </a>
        <a className="masthead-link" href="https://github.com/EnioAguiar/prove-it">
          Source on GitHub
        </a>
      </header>

      <main className="layout">
        <section className="intake" aria-labelledby="intro-title">
          <h1 id="intro-title">Don&apos;t trust the review. Run&nbsp;it.</h1>
          <p className="lede">
            Paste a Python function. Prove It writes property tests for it, runs them in a sealed
            container with no network and no secrets, and lets the exit code decide.
          </p>
          <CodeInput
            code={code}
            intent={intent}
            examples={examples}
            running={running}
            onCode={setCode}
            onIntent={setIntent}
            onSubmit={prove}
          />
        </section>

        <aside className="tape-column" aria-label="Run log">
          <Timeline
            lines={lines}
            report={report}
            error={error}
            running={running}
            startedAt={startedAt}
          />
        </aside>

        {report && <Findings report={report} />}
      </main>
    </div>
  );
}
