const HEADER = /(?:Falsifying example|Failing test case):\s*(.*)$/;
const PYTEST_PREFIX = /^E?\s*/;
const MAX_LINES = 30;

/**
 * The smallest failing input Hypothesis found, as it printed it.
 * Hypothesis 6.170 writes `Failing test case: test_x(`, older versions `Falsifying example: test_x(`,
 * then one `name=value,` line per argument and a closing `)`. pytest may prefix lines with `E`.
 */
export function findCounterexample(output: string): string | null {
  const lines = output.split("\n");
  const start = lines.findIndex((line) => HEADER.test(line));
  if (start === -1) return null;
  const first = HEADER.exec(lines[start])![1].trim();
  if (!first.endsWith("(")) return first || null;
  const picked = [first];
  for (const raw of lines.slice(start + 1, start + 1 + MAX_LINES)) {
    const line = raw.replace(PYTEST_PREFIX, "");
    if (line.startsWith(")")) {
      picked.push(")");
      return picked.join("\n");
    }
    picked.push(`    ${line.trim()}`);
  }
  return null;
}
