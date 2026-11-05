import type { ContainmentEvent, EventKind } from "@/lib/types";

const LABELS: Record<EventKind, string> = {
  write_blocked: "Write to disk blocked",
  network_blocked: "Network unreachable",
  process_limit: "Process limit hit",
  memory_limit: "Memory cap hit",
  timeout: "Killed at the time limit",
  secret_absent: "API key not present",
};

export default function ContainmentPanel({ events }: { events: ContainmentEvent[] }) {
  return (
    <section className="finding containment" aria-labelledby="containment-title">
      <h2 id="containment-title">What the sandbox stopped</h2>
      <p className="finding-note">
        Each line below was printed by the run itself. The container was destroyed afterwards.
      </p>
      <ul className="containment-list">
        {events.map((event) => (
          <li key={event.kind}>
            <span className="containment-kind">{LABELS[event.kind]}</span>
            <code className="containment-evidence">{event.evidence}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}
