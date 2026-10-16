# OpenFE demo

Standalone demo site using the sibling `repo-history-viz` library and reports from `../openfe/`.

Run `npm start` in this directory, then open http://127.0.0.1:4175/openfe.html. Startup rebuilds the visualizer and copies its standalone bundle and generates one summary manifest into `dist/`. Restart after changing the visualizer or adding reports. The build uses the repository’s Pixi Python environment to validate reports and generate the manifest; install the visualizer dependencies first with `npm install --prefix ../repo-history-viz`.

Run `npm run build` to produce a self-contained static site in `dist/`, suitable for any static server. Run `node scripts/serve.mjs` to serve an existing build without rebuilding. The server binds to loopback and defaults to port 4175; set `PORT` to override it.

The visualizer's own generic demo and browser tests are independent of this site. Its browser tests use port 4174.

The reports are being backfilled. Each build discovers the monthly reports in `../openfe/` and generates a fresh summary manifest in `dist/demo-reports/manifest.json`. The configured source must contain at least one report.
