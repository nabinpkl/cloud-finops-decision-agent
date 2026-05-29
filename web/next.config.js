/** @type {import('next').NextConfig} */

// Origin of the FastAPI backend. The browser only ever talks to this Next
// server (same-origin); /assistant requests are proxied to the backend, so
// there is no CORS round-trip and no backend URL in client code.
const backendOrigin = process.env.BACKEND_ORIGIN || "http://localhost:8000";

const nextConfig = {
  experimental: {
    optimizePackageImports: ["@assistant-ui/react"],
  },
  async rewrites() {
    return [
      {
        source: "/assistant/:path*",
        destination: `${backendOrigin}/assistant/:path*`,
      },
    ];
  },
};

export default nextConfig;
