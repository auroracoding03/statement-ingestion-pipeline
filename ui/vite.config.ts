import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

// Two builds from one codebase:
//   default  -> ui/dist, served by FastAPI, full read/write against /api
//   static   -> dashboard/dist, read-only, reads ./data/*.json for Cloudflare Pages
export default defineConfig(({ mode }) => {
  const isStatic = mode === "static";
  return {
    plugins: [react()],
    base: isStatic ? "./" : "/",
    define: {
      __DATA_MODE__: JSON.stringify(isStatic ? "static" : "live"),
    },
    build: {
      outDir: isStatic ? resolve(root, "../dashboard/dist") : resolve(root, "dist"),
      emptyOutDir: true,
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8787",
          changeOrigin: true,
        },
      },
    },
  };
});
