import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    outDir: "demo-dist",
    rolldownOptions: { input: ["index.html"] },
  },
  server: {
    // The canonical report schema lives in the sibling Python package.
    fs: { allow: [".."] },
  },
});
