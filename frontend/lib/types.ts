// Mirrors app/report.py and the dataclasses it nests (serialised with dataclasses.asdict).

export type Step =
  | "parse"
  | "tests"
  | "gate"
  | "run"
  | "fix"
  | "rerun"
  | "mutation"
  | "explain"
  | "done";
export type StepStatus = "start" | "ok" | "fail";
export type Verdict = "PASS" | "FAIL" | "ERROR";

export interface StepEvent {
  step: Step;
  status: StepStatus;
  detail: string;
}

export interface RunResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_s: number;
  timed_out: boolean;
  container_name: string;
}

export type EventKind =
  | "write_blocked"
  | "network_blocked"
  | "process_limit"
  | "memory_limit"
  | "timeout"
  | "secret_absent";

export interface ContainmentEvent {
  kind: EventKind;
  evidence: string;
}

export interface Mutant {
  id: number;
  line: number;
  description: string;
  source: string;
}

export interface MutationResult {
  total: number;
  killed: number;
  survivors: Mutant[];
}

export interface Receipt {
  code_sha256: string;
  tests_sha256: string;
  image: string;
  image_digest: string;
  runtime: string;
  limits: Record<string, string>;
  exit_code: number;
  duration_s: number;
  command: string;
  created_at: string;
}

export interface Report {
  verdict: Verdict;
  error: string | null;
  code: string;
  tests: string;
  original_run: RunResult | null;
  events: ContainmentEvent[];
  fix: string | null;
  fix_run: RunResult | null;
  mutation: MutationResult | null;
  explanation: string;
  receipt: Receipt | null;
}

export interface Example {
  id: string;
  label: string;
  code: string;
  intent: string;
}
