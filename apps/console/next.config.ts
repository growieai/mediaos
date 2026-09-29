import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone", poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: [
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
    ] }];
  },
};
export default config;
