import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Тесты: jsdom — компонентные (App/гостевой режим), чистые хелперы совместимы.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
  },
});
