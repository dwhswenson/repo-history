// Exercise the tarball in a fresh consumer with no source-checkout dependencies.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const temporary = mkdtempSync(join(tmpdir(), "repo-history-package-"));
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const npm = process.env.npm_execpath;
assert(npm, "Run this check with npm run test:package");
const runNode = (args, cwd = temporary) =>
  execFileSync(process.execPath, args, {
    cwd,
    encoding: "utf8",
    stdio: "pipe",
  });
const runNpm = (args, cwd = temporary) => runNode([npm, ...args], cwd);

try {
  const [packed] = JSON.parse(
    runNpm(
      ["pack", "--ignore-scripts", "--json", "--pack-destination", temporary],
      root,
    ),
  );
  const paths = new Set(packed.files.map(({ path }) => path));
  const declarations = ["index", "register", "component", "report"].map(
    (name) => `dist/types/${name}.d.ts`,
  );
  for (const required of [
    "package.json",
    "README.md",
    "LICENSE",
    "dist/index.js",
    "dist/register.js",
    "dist/repo-history-viz.js",
    "dist/THIRD_PARTY_LICENSES.md",
    ...declarations,
  ])
    assert(paths.has(required), `Missing published file: ${required}`);
  for (const path of paths)
    assert(
      [
        "package.json",
        "README.md",
        "LICENSE",
        "dist/THIRD_PARTY_LICENSES.md",
        ...declarations,
      ].includes(path) || /^dist\/[^/]+\.js$/.test(path),
      `Unexpected published file: ${path}`,
    );
  assert.equal(
    Object.keys(manifest.dependencies ?? {}).length,
    0,
    "All runtime libraries are bundled; consumers should install no dependencies",
  );
  for (const entry of Object.values(manifest.exports)) {
    for (const target of Object.values(entry))
      assert(
        paths.has(target.replace(/^\.\//, "")),
        `Missing export target: ${target}`,
      );
  }

  writeFileSync(
    join(temporary, "package.json"),
    JSON.stringify({ private: true, type: "module" }),
  );
  runNpm([
    "install",
    join(temporary, packed.filename),
    "--ignore-scripts",
    "--offline",
    "--omit=dev",
    "--no-audit",
    "--no-fund",
    "--cache",
    join(temporary, "cache"),
  ]);
  const installed = join(temporary, "node_modules", manifest.name);
  const notices = readFileSync(
    join(installed, "dist/THIRD_PARTY_LICENSES.md"),
    "utf8",
  );
  for (const library of ["chart.js", "ajv", "ajv-formats", "@kurkle/color"])
    assert(
      notices.includes(library),
      `Missing bundled library notice: ${library}`,
    );
  assert(
    notices.includes("Permission is hereby granted"),
    "License texts must be included",
  );

  // No browser globals: both published entry points must be safe to import in SSR.
  runNode([
    "--input-type=module",
    "-e",
    `
    import assert from "node:assert/strict";
    const library = await import(${JSON.stringify(manifest.name)});
    assert.equal(typeof library.RepoHistoryViz, "function");
    library.defineRepoHistoryViz();
    await import(${JSON.stringify(`${manifest.name}/register`)});
  `,
  ]);
  // Browser registration must use the same class exported by the npm entry point.
  runNode([
    "--input-type=module",
    "-e",
    `
    import assert from "node:assert/strict";
    globalThis.HTMLElement = class {};
    const registry = new Map();
    globalThis.customElements = { get: key => registry.get(key), define: (key, value) => {
      assert(!registry.has(key), "Duplicate registration"); registry.set(key, value);
    }};
    const library = await import(${JSON.stringify(manifest.name)});
    assert.equal(registry.size, 0, "Main import must not register automatically");
    await import(${JSON.stringify(`${manifest.name}/register`)});
    assert.equal(registry.get("repo-history-viz"), library.RepoHistoryViz);
    library.defineRepoHistoryViz();
    assert.equal(registry.size, 1);
  `,
  ]);
  // Verify the copyable static bundle works without imports from the npm entry.
  runNode([
    "--input-type=module",
    "-e",
    `
    import assert from "node:assert/strict";
    import { pathToFileURL } from "node:url";
    globalThis.HTMLElement = class {};
    const registry = new Map();
    globalThis.customElements = { get: key => registry.get(key), define: (key, value) => registry.set(key, value) };
    await import(pathToFileURL(${JSON.stringify(join(installed, "dist/repo-history-viz.js"))}));
    assert.equal(typeof registry.get("repo-history-viz"), "function");
  `,
  ]);

  writeFileSync(
    join(temporary, "consumer.mts"),
    `
    import { RepoHistoryViz, defineRepoHistoryViz, type HistorySummarySource, type RepoHistoryLoadDetail } from ${JSON.stringify(manifest.name)};
    import ${JSON.stringify(`${manifest.name}/register`)};
    defineRepoHistoryViz();
    const viewer: RepoHistoryViz | null = document.querySelector("repo-history-viz");
    const source: HistorySummarySource = { name: "summary.json", text: "{}" };
    const result: Promise<void> | undefined = viewer?.loadSummarySource(source);
    const months: RepoHistoryLoadDetail["months"] = [];
    void result; void months;
  `,
  );
  runNode([
    join(root, "node_modules/typescript/bin/tsc"),
    "--noEmit",
    "--strict",
    "--skipLibCheck",
    "false",
    "--module",
    "NodeNext",
    "--moduleResolution",
    "NodeNext",
    "--target",
    "ES2022",
    "--lib",
    "ES2023,DOM",
    "consumer.mts",
  ]);
  console.log(
    `Package verified: ${packed.name}@${packed.version}, ${paths.size} files, ${packed.size} packed bytes`,
  );
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
