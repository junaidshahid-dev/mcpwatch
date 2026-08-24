/**
 * The browser always talks to THIS origin (the Next app). Requests to the API, badges and public
 * status are rewritten (proxied) to the FastAPI backend at API_ORIGIN. Because it's a rewrite,
 * not a redirect, the backend's httpOnly session cookie is set on this origin — so the existing
 * same-origin cookie auth keeps working with no CORS or SameSite=None needed.
 *
 * Set API_ORIGIN in the environment (Vercel project settings) to the deployed backend URL.
 * @type {import('next').NextConfig}
 */
const API_ORIGIN = process.env.API_ORIGIN || "http://localhost:8000";

const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` },
      { source: "/badge/:path*", destination: `${API_ORIGIN}/badge/:path*` },
      { source: "/status/:path*", destination: `${API_ORIGIN}/status/:path*` },
      { source: "/webhooks/:path*", destination: `${API_ORIGIN}/webhooks/:path*` },
    ];
  },
};

export default nextConfig;
