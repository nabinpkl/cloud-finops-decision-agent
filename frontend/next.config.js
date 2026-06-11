/** @type {import('next').NextConfig} */

// Origin of the FastAPI backend. The browser only ever talks to this Next
// server (same-origin); /assistant requests are proxied to the backend, so
// there is no CORS round-trip and no backend URL in client code.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
  async rewrites() {
    return [
      // AG-UI transport: the HttpAgent POSTs to /assistant (no subpath).
      {
        source: "/assistant",
        destination: `${backendOrigin}/assistant`,
      },
      {
        source: "/assistant/:path*",
        destination: `${backendOrigin}/assistant/:path*`,
      },
      // Citation excerpt-on-click verification hunk (ADR-0008): same-origin so
      // the browser never holds the backend URL.
      {
        source: "/citation/:path*",
        destination: `${backendOrigin}/citation/:path*`,
      },
    ];
  },
};

export default nextConfig;
