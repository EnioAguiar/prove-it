import type { Report, StepEvent } from "./types";

export interface ReviewHandlers {
  onStep: (event: StepEvent) => void;
  onReport: (report: Report) => void;
}

/** A failure the user can act on, already worded for the screen. */
export class ReviewError extends Error {}

const HTTP_MESSAGES: Record<string, string> = {
  busy: "Another review is running right now. Try again in a minute.",
  "rate limited": "You ran 10 reviews in the last 10 minutes. Wait a few minutes and try again.",
  "code too large": "The code is over 20,000 bytes. Paste a smaller piece.",
};

async function httpError(response: Response): Promise<ReviewError> {
  let detail = "";
  try {
    const body = await response.json();
    detail = typeof body.detail === "string" ? body.detail : "";
  } catch {
    // Not JSON (e.g. a proxy error page); fall through to the status code.
  }
  return new ReviewError(
    HTTP_MESSAGES[detail] ?? `The server answered ${response.status}. Try again in a minute.`,
  );
}

/**
 * POST /api/review and read its Server-Sent Events.
 * EventSource only does GET, so the stream is parsed by hand: blocks separated by a blank line,
 * `event:` and `data:` fields, `:` comment lines (keep-alive pings) ignored.
 */
export async function streamReview(
  code: string,
  intent: string,
  handlers: ReviewHandlers,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/review", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ code, intent: intent.trim() || null }),
      signal,
    });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ReviewError("Can't reach the server. Check your connection and try again.");
  }
  if (!response.ok || !response.body) throw await httpError(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  let gotReport = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n?/g, "\n");
    let end: number;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length === 0) continue;
      const payload = JSON.parse(data.join("\n"));
      if (event === "step") handlers.onStep(payload as StepEvent);
      else if (event === "report") {
        gotReport = true;
        handlers.onReport(payload as Report);
      }
    }
  }
  if (!gotReport) {
    throw new ReviewError("The connection closed before the review finished. Try again.");
  }
}
