import type { Metadata } from "next";
import Verifier from "@/components/Verifier";

export const metadata: Metadata = {
  title: "Verify a receipt · Prove It",
  description: "Check that a Prove It receipt is exactly what the server signed after the run.",
};

export default function VerifyPage() {
  return <Verifier />;
}
