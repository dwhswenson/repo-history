import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  build: {
    outDir: "dist",
    copyPublicDir: false,
    emptyOutDir: mode !== "standalone",
    target: "es2022",
    license: { fileName: "THIRD_PARTY_LICENSES.md" },
    lib: {
      entry:
        mode === "standalone"
          ? "src/register.ts"
          : { index: "src/index.ts", register: "src/register.ts" },
      formats: ["es"],
      fileName: (_format, entryName) =>
        mode === "standalone" ? "repo-history-viz.js" : `${entryName}.js`,
    },
    minify: true,
  },
}));
