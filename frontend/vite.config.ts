/// <reference types="vitest" />
import { configDefaults } from "vitest/config";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Dev server: http://127.0.0.1:5173 (the origin the backend's CORS allows).
// When VITE_API_BASE_URL is empty, the app calls same-origin paths and this proxy forwards
// /api and /health to the FastAPI server (VITE_PROXY_TARGET, default http://127.0.0.1:8000).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "VITE_");
  const target = env.VITE_PROXY_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": { target, changeOrigin: false },
        "/health": { target, changeOrigin: false },
      },
    },
    build: { sourcemap: false, chunkSizeWarningLimit: 600 },
    test: {
      environment: "jsdom",
      // A real origin, so window.localStorage exists and tests can prove nothing is written to it.
      environmentOptions: { jsdom: { url: "http://127.0.0.1:5173/" } },
      setupFiles: ["./src/test/setup.ts"],
      exclude: configDefaults.exclude,
      css: false,
      // Light on memory: this machine has hit "paging file too small" before.
      pool: "forks",
      poolOptions: { forks: { maxForks: 2, minForks: 1 } },
      testTimeout: 15000,
      restoreMocks: true,
    },
  };
});
