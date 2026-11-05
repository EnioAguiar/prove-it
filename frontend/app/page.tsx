import { readFile } from "node:fs/promises";
import path from "node:path";
import ProveIt from "@/components/ProveIt";
import type { Example } from "@/lib/types";

// Read at build time from the repo's examples/ folder, so the page and the golden-path check
// always use the same files.
const EXAMPLES: Omit<Example, "code">[] = [
  {
    id: "parcelamento",
    label: "Installments bug",
    intent: "Split a purchase into n installments that add up exactly to the total.",
  },
  { id: "hostile", label: "Hostile code", intent: "Count the words in a string." },
  { id: "hostile_loop", label: "Infinite loop", intent: "" },
];

async function loadExamples(): Promise<Example[]> {
  const dir = path.join(process.cwd(), "..", "examples");
  return Promise.all(
    EXAMPLES.map(async (example) => ({
      ...example,
      code: await readFile(path.join(dir, `${example.id}.py`), "utf8"),
    })),
  );
}

export default async function Page() {
  return <ProveIt examples={await loadExamples()} />;
}
