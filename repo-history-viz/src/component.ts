import { createViewer } from "./viewer.js";
import type { ReportSource } from "./report.js";

// Importing the package during static rendering must not require a browser DOM.
const ElementBase = globalThis.HTMLElement ?? (class {} as typeof HTMLElement);

/** Interactive monthly repository history, isolated in an open shadow root. */
export class RepoHistoryViz extends ElementBase {
  static observedAttributes = ["manifest", "measures"];
  private viewer?: ReturnType<typeof createViewer>;

  connectedCallback(): void {
    if (!this.viewer) {
      const root = this.attachShadow({ mode: "open" });
      this.viewer = createViewer(root, (type, detail) => {
        this.dispatchEvent(
          new CustomEvent(type, { detail, bubbles: true, composed: true }),
        );
      });
    }
    this.viewer.setMeasures(this.getAttribute("measures"));
    const manifest = this.getAttribute("manifest");
    if (manifest?.trim()) void this.viewer.loadUrl(manifest).catch(() => {});
  }

  disconnectedCallback(): void {
    this.viewer?.disconnect();
  }

  attributeChangedCallback(
    name: string,
    previous: string | null,
    value: string | null,
  ): void {
    if (previous === value || !this.isConnected || !this.viewer) return;
    if (name === "measures") {
      this.viewer.setMeasures(value);
      return;
    }
    if (value?.trim()) void this.viewer.loadUrl(value).catch(() => {});
    else this.viewer.cancel();
  }

  /** Load a hosted manifest. Rejects on failure; the previous dataset is retained. */
  loadManifest(url: string): Promise<void> {
    if (!this.isConnected || !this.viewer)
      return Promise.reject(
        new Error("Connect the viewer before loading reports."),
      );
    return this.viewer.loadUrl(url);
  }

  /** Load report JSON strings without a network request. */
  loadReports(sources: ReportSource[]): Promise<void> {
    if (!this.isConnected || !this.viewer)
      return Promise.reject(
        new Error("Connect the viewer before loading reports."),
      );
    return this.viewer.loadSources(sources);
  }
}

/** Register the standard element once. Safe to call during server-side rendering. */
export function defineRepoHistoryViz(): void {
  if (
    typeof customElements !== "undefined" &&
    !customElements.get("repo-history-viz")
  ) {
    customElements.define("repo-history-viz", RepoHistoryViz);
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "repo-history-viz": RepoHistoryViz;
  }
}
