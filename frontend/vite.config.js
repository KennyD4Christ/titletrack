import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// IMPORTANT: do NOT add `changeOrigin: true` to the proxy. Django's CSRF check compares the browser's
// Origin (localhost:5173) with the Host it receives; keeping the original Host makes them match.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.js",
    css: false,
  },
});
