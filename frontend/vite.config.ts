import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The dev server proxies /api to the backend so the frontend runs on exactly the same
// origin the built app will. Splitting them in development would hide every CSRF,
// cookie and Host problem until production, which is the opposite of useful.
const BACKEND = process.env.HEXTON_BACKEND ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: false },
      "/oauth": { target: BACKEND, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    // The bundle is served by the local API from the same origin, so there is no CDN
    // and no subresource-integrity story to maintain.
    target: "es2022",
  },
});
