import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  build: {
    outDir: "dist",
    copyPublicDir: false,
    emptyOutDir: mode !== "standalone",
    target: "es2022",
    lib: {
      entry: mode === "standalone" ? "src/register.ts" : "src/index.ts",
      formats: ["es"],
      fileName: () =>
        mode === "standalone" ? "repo-history-viz.js" : "index.js",
    },
    minify: true,
  },
}));
