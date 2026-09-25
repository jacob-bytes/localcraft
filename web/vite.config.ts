import { rmSync } from "node:fs";
import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type Plugin } from "vite";

/**
 * `public/mockServiceWorker.js` is served in dev so MSW can register its
 * service worker. It must never ship: CONTRACT §11 #11 verifies that
 * `web/dist` contains no `msw` string at all, and the worker file alone would
 * fail that grep. Delete it from the output on production builds.
 */
function stripMockWorker(outDir: string): Plugin {
  return {
    name: "localcraft:strip-mock-service-worker",
    apply: "build",
    enforce: "post",
    closeBundle() {
      rmSync(`${outDir}/mockServiceWorker.js`, { force: true });
    },
  };
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  // Mocks are opt-in. The literal is injected at build time so that the whole
  // `if (__ENABLE_MOCKS__)` branch — including the dynamic import of MSW — is
  // dead-code eliminated from production bundles (CONTRACT §11 #11).
  const enableMocks = env.VITE_ENABLE_MOCKS === "true";
  const outDir = "dist";

  return {
    plugins: [
      react(),
      tailwindcss(),
      ...(enableMocks ? [] : [stripMockWorker(outDir)]),
    ],
    define: {
      __ENABLE_MOCKS__: JSON.stringify(enableMocks),
    },
    resolve: {
      alias: {
        "@": fileURLToPath(new URL("./src", import.meta.url)),
      },
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: false,
        },
      },
    },
    build: {
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ["react", "react-dom", "react-router-dom"],
          },
        },
      },
    },
  };
});
