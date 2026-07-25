/** @type {import('next').NextConfig} */
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8010";

const nextConfig = {
  // The API client ships as raw TS from the local monorepo package.
  transpilePackages: ["@danceseek/api-client"],

  // Set cover art comes from the linked YouTube recording's thumbnail — the
  // only artwork we have, and free.
  images: {
    remotePatterns: [{ protocol: "https", hostname: "i.ytimg.com", pathname: "/vi/**" }],
  },

  // Proxy the API under the web app's own origin. This is what makes the
  // session cookie work: the browser only ever talks to one origin, so the
  // cookie is same-site in dev and in production and no third-party-cookie
  // rules apply. Server-side rendering still calls API_URL directly.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};
export default nextConfig;
