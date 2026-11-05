import type { NextConfig } from "next";

// Static export: Caddy serves `out/` and proxies `/api/*` to FastAPI on the same origin.
const nextConfig: NextConfig = {
  output: "export",
};

export default nextConfig;
