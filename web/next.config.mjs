/** @type {import('next').NextConfig} */
const isDev = process.env.NODE_ENV !== "production";

const nextConfig = {
  distDir: isDev ? ".next-dev" : ".next",
  output: isDev ? undefined : "standalone",
  experimental: {
    typedRoutes: false
  }
};

export default nextConfig;
