import { defineConfig } from "vite";
export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: Number(process.env.G1_UI_PORT || 5173),
    strictPort: true,
    proxy: { "/api": `http://127.0.0.1:${process.env.G1_CONSOLE_PORT || 8765}` },
  },
});
