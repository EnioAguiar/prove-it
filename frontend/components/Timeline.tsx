"use client";

import { useEffect, useRef } from "react";
import ReceiptView from "./ReceiptView";
import VerdictCard from "./VerdictCard";
import type { Report, Step, StepEvent } from "@/lib/types";

export interface TapeLine extends StepEvent {
  /** Seconds since the run started. */
  at: number;
  key: number;
}

const STEP_LABELS: Record<Step, string> = {
  parse: "Read code",
  tests: "Write tests",
  gate: "Check tests",
  run: "Run tests",
  fix: "Propose fix",
  rerun: "Run fix",
  mutation: "Sabotage",
  explain: "Explain",
  done: "Done",
};

const STATUS_TEXT = { start: "", ok: "ok", fail: "FAIL" } as const;

function elapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${(seconds % 60).toFixed(1).padStart(4, "0")}`;
}

interface Props {
  lines: TapeLine[];
  report: Report | null;
  error: string | null;
  running: boolean;
  startedAt: number | null;
  queuePosition: number | null;
}

export default function Timeline({ lines, report, error, running, startedAt, queuePosition }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const idle = !running && lines.length === 0 && !report && !error;

  useEffect(() => {
    const box = scroller.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [lines.length, report, error]);

  return (
    <div className="tape" ref={scroller}>
      <div className="tape-head">
        <p className="tape-title">Prove It · run log</p>
        <p>
          {startedAt
            ? new Date(startedAt).toISOString().replace("T", " ").slice(0, 19) + " UTC"
            : "waiting for code"}
        </p>
        <p>no network · 256 MB · 10 s limit</p>
      </div>

      {idle && (
        <p className="tape-empty">
          Nothing printed yet. Paste code or load an example, then press Prove it.
        </p>
      )}

      {queuePosition !== null && (
        <p className="tape-empty" aria-live="polite">
          {queuePosition === 1
            ? "Every sandbox is busy. You're next in line."
            : `Every sandbox is busy. You're #${queuePosition} in line.`}
          <span className="tape-cursor" />
        </p>
      )}

      {lines.length > 0 && (
        <ol className="tape-lines" aria-live="polite">
          {lines
            .filter((line) => line.step !== "done")
            .map((line) => (
              <li key={line.key} className={`tape-line status-${line.status}`}>
                <span className="tape-time">{elapsed(line.at)}</span>
                <span className="tape-step">{STEP_LABELS[line.step]}</span>
                <span className="tape-status">
                  {line.status === "start" ? <span className="tape-cursor" /> : STATUS_TEXT[line.status]}
                </span>
                {line.detail && line.step !== "parse" && (
                  <span className="tape-detail">{line.detail}</span>
                )}
              </li>
            ))}
        </ol>
      )}

      {error && (
        <p className="tape-error" role="alert">
          {error}
        </p>
      )}

      {report && (
        <>
          <VerdictCard verdict={report.verdict} error={report.error} run={report.original_run} />
          {report.receipt && <ReceiptView receipt={report.receipt} />}
        </>
      )}
    </div>
  );
}
