import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite is the development server and build tool for the frontend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The proxy: any request from the page to /api/... is forwarded to the
    // FastAPI backend on port 8000. The browser only ever talks to 5173,
    // so we avoid cross-origin (CORS) setup entirely during development.
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
