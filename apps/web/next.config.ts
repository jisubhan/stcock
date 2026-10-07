import type { NextConfig } from 'next';
import { resolve } from 'node:path';
const apiOrigin = process.env.API_ORIGIN || 'http://127.0.0.1:8000';
const nextConfig: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: resolve(__dirname, '../..'),
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
  async rewrites() { return [{ source: '/api/:path*', destination: `${apiOrigin}/api/:path*` }]; },
  devIndicators: false,
};
export default nextConfig;
