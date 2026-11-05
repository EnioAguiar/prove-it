import { readFile } from "node:fs/promises";
import path from "node:path";
import ProveIt from "@/components/ProveIt";
import type { Example } from "@/lib/types";

// Read at build time from the repo's examples/ folder, the same files and intents that
// scripts/golden_path.py sends.
async function loadExamples(): Promise<Example[]> {
  const dir = path.join(process.cwd(), "..", "examples");
  const index: Omit<Example, "code">[] = JSON.parse(await readFile(path.join(dir, "index.json"), "utf8"));
  return Promise.all(
    index.map(async (example) => ({
      ...example,
      code: await readFile(path.join(dir, `${example.id}.py`), "utf8"),
    })),
  );
}

export default async function Page() {
  return <ProveIt examples={await loadExamples()} />;
}
