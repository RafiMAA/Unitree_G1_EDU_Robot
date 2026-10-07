import { defineConfig } from "vite";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.dirname(fileURLToPath(import.meta.url));
const key = process.env.G1_TLS_KEY || path.join(root, ".phone-tls/server.key");
const cert = process.env.G1_TLS_CERT || path.join(root, ".phone-tls/server.crt");
const https = fs.existsSync(key) && fs.existsSync(cert) ? { key: fs.readFileSync(key), cert: fs.readFileSync(cert) } : undefined;
export default defineConfig({
  server: {
    host: process.env.G1_UI_HOST || (https ? "0.0.0.0" : "127.0.0.1"),
    https,
    port: Number(process.env.G1_UI_PORT || 5173),
    strictPort: true,
    proxy: {
      "/api/rag/live": { target: `ws://127.0.0.1:${process.env.G1_RAG_LIVE_PORT || (Number(process.env.G1_RAG_PORT || 8767) + 1)}`, ws: true },
      "/api": `http://127.0.0.1:${process.env.G1_CONSOLE_PORT || 8765}`,
      "/rosbridge": { target: `ws://127.0.0.1:${process.env.G1_ROSBRIDGE_PORT || 9090}`, ws: true, rewrite: p => p.replace(/^\/rosbridge/, "") || "/" },
    },
  },
});
