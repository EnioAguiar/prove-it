/** sessionStorage key the receipt travels under from the review page to /verify/. */
export const VERIFY_KEY = "proveit-receipt";

export interface VerifyResult {
  valid: boolean;
  reason: string;
}

export async function verifyReceipt(text: string): Promise<VerifyResult> {
  let receipt: unknown;
  try {
    receipt = JSON.parse(text);
  } catch {
    return { valid: false, reason: "this is not valid JSON" };
  }
  if (typeof receipt !== "object" || receipt === null || Array.isArray(receipt)) {
    return { valid: false, reason: "a receipt is a JSON object" };
  }
  const response = await fetch("/api/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(receipt),
  });
  if (!response.ok) throw new Error(`The server answered ${response.status}. Try again.`);
  return response.json();
}
