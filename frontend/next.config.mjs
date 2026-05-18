/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy /api/* requests to the FastAPI backend so the dashboard makes
  // same-origin requests and we don't need CORS configured on the API.
  // Override the target with `AGENTATLAS_API_URL` if the backend isn't on
  // localhost:8000 (e.g. when running both in Docker on a shared network).
  async rewrites() {
    const apiUrl = process.env.AGENTATLAS_API_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${apiUrl}/:path*`,
      },
    ];
  },
  // No server-side images, no analytics, no telemetry. Keep the demo
  // build minimal.
  poweredByHeader: false,
  reactStrictMode: true,
};

export default nextConfig;
