import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Mini App dev-сервер (MINIAPP_URL=http://localhost:8080).
// /api проксируется на локальный backend (ТЗ п. 4.3).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8080,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
