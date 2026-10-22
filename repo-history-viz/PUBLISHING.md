# Publishing

The initial release is `@dwhswenson/repo-history-viz@0.1.0`. Publish from the `repo-history-viz` directory using an npm account authorized for the `@dwhswenson` scope, with npm’s required two-factor authentication configured.

1. Run `npm ci`, `npm run format:check`, `npm test`, `npm run test:package`, and `npm run test:browser`.
2. Review `npm pack --dry-run` and the source changes, commit the release, and ensure CI passes. Version numbers cannot be reused after publication.
3. Authenticate with `npm login --registry=https://registry.npmjs.org/`, then run `npm publish` when ready. The manifest sets public access and the npm registry explicitly. `prepublishOnly` reruns format, unit, and isolated package checks; `prepack` rebuilds the published assets.

Nothing publishes automatically. For future automated releases, configure [npm trusted publishing](https://docs.npmjs.com/trusted-publishers/) for a dedicated GitHub Actions workflow rather than saving a long-lived publish token. Trusted publishing can generate provenance for a public repository; confirm repository visibility and publisher setup before enabling it.
