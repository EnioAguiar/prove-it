import type { NextConfig } from "next";

// Static export: Caddy serves `out/` and proxies `/api/*` to FastAPI on the same origin.
const nextConfig: NextConfig = {
  output: "export",
  // verify/index.html, so Caddy's file_server serves /verify/ without rewrite rules.
  trailingSlash: true,
};

export default nextConfig;
