import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Relative base: the build is served by FastAPI under /app/.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
