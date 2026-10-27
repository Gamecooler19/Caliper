import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev-server proxy mirrors Caddy's production routing: /api/v1/* is the
// only backend path the frontend ever calls, as a same-origin relative
// request. In dev this proxies straight to the FastAPI container/process
// on :8000; it never attaches an API key -- that is Caddy's job alone in
// every real deployment (see ../Caddyfile). Local `npm run dev` against a
// real backend therefore still requires the backend to accept the request
// (e.g. a developer temporarily relaxing auth, or running behind a local
// Caddy) -- this proxy exists for routing convenience, not to reintroduce
// client-side key handling.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      "/api/v1": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
