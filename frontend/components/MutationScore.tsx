import type { MutationResult } from "@/lib/types";

export default function MutationScore({
  mutation,
  target,
}: {
  mutation: MutationResult;
  target: "your code" | "the proposed fix";
}) {
  // Only survivors come back with details, so the row is drawn caught-first.
  const cells = Array.from({ length: mutation.total }, (_, i) => i < mutation.killed);

  return (
    <section className="finding" aria-labelledby="mutation-title">
      <h2 id="mutation-title">
        {mutation.killed} of {mutation.total} sabotages caught
      </h2>
      <p className="finding-note">
        Prove It made {mutation.total} small changes to {target} (for example <code>+</code> to{" "}
        <code>-</code>, <code>&lt;</code> to <code>&lt;=</code>) and reran the same tests. A change the
        tests don&apos;t notice is a gap in the tests, not a bug in the code.
      </p>
      {mutation.total > 0 && (
        <div className="sabotage-row" aria-hidden="true">
          {cells.map((caught, i) => (
            <span key={i} className={caught ? "sabotage caught" : "sabotage missed"} />
          ))}
        </div>
      )}
      {mutation.survivors.length > 0 && (
        <ul className="survivors">
          {mutation.survivors.map((mutant) => (
            <li key={mutant.id}>
              <span className="survivor-line">line {mutant.line}</span> {mutant.description}{" "}
              <span className="survivor-tag">slipped through</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
