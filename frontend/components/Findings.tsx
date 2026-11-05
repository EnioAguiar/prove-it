"use client";

import ContainmentPanel from "./ContainmentPanel";
import MutationScore from "./MutationScore";
import { findCounterexample } from "@/lib/counterexample";
import type { Report, RunResult } from "@/lib/types";

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/x-python" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function runOutput(run: RunResult): string {
  return [run.stdout, run.stderr].filter((part) => part.trim()).join("\n");
}

export default function Findings({ report }: { report: Report }) {
  const run = report.original_run;
  const counterexample = run ? findCounterexample(runOutput(run)) : null;
  const fixPassed = report.fix_run?.exit_code === 0 && !report.fix_run.timed_out;
  const mutationTarget = report.verdict === "PASS" ? "your code" : "the proposed fix";
  const empty =
    !report.explanation && !counterexample && report.fix === null && report.events.length === 0 &&
    !report.mutation && !report.tests && !run;
  if (empty) return null;

  return (
    <section className="findings" aria-label="Findings">
      {report.explanation && (
        <section className="finding" aria-labelledby="explain-title">
          <h2 id="explain-title">What happened</h2>
          <p className="finding-note">Written by the model from the run results. It cannot change the verdict.</p>
          <p className="explanation">{report.explanation}</p>
        </section>
      )}

      {counterexample && (
        <section className="finding" aria-labelledby="counter-title">
          <h2 id="counter-title">Smallest input that breaks it</h2>
          <p className="finding-note">Hypothesis shrank the failing case down to this.</p>
          <pre className="code-block counterexample">{counterexample}</pre>
        </section>
      )}

      {report.fix !== null && (
        <section className="finding" aria-labelledby="fix-title">
          <div className="finding-head">
            <h2 id="fix-title">Proposed fix</h2>
            <span className={fixPassed ? "badge badge-pass" : "badge badge-fail"}>
              {fixPassed ? "passes the same tests" : "still fails"}
            </span>
          </div>
          <p className="finding-note">
            Written by the model and run in a new container against the unchanged tests. Your
            code&apos;s verdict stays as it was.
          </p>
          <pre className="code-block">{report.fix}</pre>
          <button type="button" className="plain-button" onClick={() => download("target_fixed.py", report.fix!)}>
            Download target_fixed.py
          </button>
        </section>
      )}

      {report.events.length > 0 && <ContainmentPanel events={report.events} />}

      {report.verdict === "FAIL" && report.fix === null && report.events.length > 0 && (
        <p className="finding-aside">
          No fix was attempted: code that tries to break out of the sandbox doesn&apos;t get one.
        </p>
      )}

      {report.mutation && <MutationScore mutation={report.mutation} target={mutationTarget} />}

      {report.tests && (
        <section className="finding" aria-labelledby="tests-title">
          <h2 id="tests-title">The tests it wrote</h2>
          <p className="finding-note">
            Frozen after the first valid run. The fix was checked against these exact bytes.
          </p>
          <details className="disclosure">
            <summary>Show test_target.py</summary>
            <pre className="code-block">{report.tests}</pre>
          </details>
          <div className="download-row">
            <button type="button" className="plain-button" onClick={() => download("target.py", report.code)}>
              Download target.py
            </button>
            <button type="button" className="plain-button" onClick={() => download("test_target.py", report.tests)}>
              Download test_target.py
            </button>
          </div>
        </section>
      )}

      {run && (
        <section className="finding" aria-labelledby="output-title">
          <h2 id="output-title">Raw output</h2>
          <details className="disclosure">
            <summary>
              Show what pytest printed (exit {run.exit_code}
              {run.timed_out ? ", killed at the time limit" : ""}, {run.duration_s.toFixed(2)} s)
            </summary>
            <pre className="code-block">{runOutput(run) || "(no output)"}</pre>
          </details>
        </section>
      )}
    </section>
  );
}
