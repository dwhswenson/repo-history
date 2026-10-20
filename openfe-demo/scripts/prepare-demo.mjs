import { mkdir, readdir, copyFile, unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const root = fileURLToPath(new URL("../", import.meta.url));
const visualizer = fileURLToPath(
  new URL("../../repo-history-viz/", import.meta.url),
);
const source = fileURLToPath(new URL("../../openfe/", import.meta.url));
const output = join(root, "dist");
const destination = join(output, "demo-reports");
const files = (await readdir(source))
  .filter((name) => /^\d{4}-(0[1-9]|1[0-2])\.json$/.test(name))
  .sort();
if (!files.length) throw new Error(`No monthly reports found in ${source}`);
execFileSync("npm", ["run", "build"], { cwd: visualizer, stdio: "inherit" });
await mkdir(destination, { recursive: true });
await Promise.all([
  copyFile(join(root, "openfe.html"), join(output, "openfe.html")),
  copyFile(
    join(visualizer, "dist/repo-history-viz.js"),
    join(output, "repo-history-viz.js"),
  ),
]);
execFileSync("pixi", ["run", "-e", "dev", "python", "-m", "repo_history", "summarize", "--input-directory", source, "--output", join(destination, "summary.json")], {
  cwd: fileURLToPath(new URL("../../", import.meta.url)), stdio: "inherit"
});
// Keep the published report data limited to the history summary.
for (const name of await readdir(destination)) {
  if (/^\d{4}-(0[1-9]|1[0-2])\.json$/.test(name)) await unlink(join(destination, name));
}
console.log(
  `Prepared standalone OpenFE demo with ${files.length} monthly reports.`,
);
