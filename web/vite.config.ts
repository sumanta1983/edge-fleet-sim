import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In `npm run dev` the browser talks only to Vite, which forwards /api to
// FastAPI. In Docker, nginx does the same job (see nginx.conf). Either way
// the page and the API share one origin, so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", rewrite: (path) => path.replace(/^\/api/, "") } },
  },
});
