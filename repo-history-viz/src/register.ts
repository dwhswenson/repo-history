import { defineRepoHistoryViz } from "./index.js";
defineRepoHistoryViz();
// Keep DOM augmentation available to consumers importing only the registration entry.
export type { RepoHistoryViz } from "./index.js";
