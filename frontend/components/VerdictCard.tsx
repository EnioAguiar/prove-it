import type { RunResult, Verdict } from "@/lib/types";

function meaning(verdict: Verdict, error: string | null, run: RunResult | null): string {
  if (verdict === "ERROR" || !run) return error ?? "The review could not finish, so there is no verdict.";
  if (run.timed_out) return "Killed at the time limit before the tests finished.";
  if (verdict === "PASS") return "pytest exited 0: every generated test passed on your original code.";
  return `pytest exited ${run.exit_code}: your original code failed at least one test.`;
}

interface Props {
  verdict: Verdict;
  error: string | null;
  run: RunResult | null;
}

export default function VerdictCard({ verdict, error, run }: Props) {
  return (
    <section className="verdict" aria-label={`Verdict for your code: ${verdict}`}>
      <p className="verdict-label">Your code</p>
      <p className={`stamp stamp-${verdict.toLowerCase()}`}>{verdict}</p>
      <p className="verdict-meaning">{meaning(verdict, error, run)}</p>
    </section>
  );
}
