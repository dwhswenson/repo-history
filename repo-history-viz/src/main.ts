import "./register.js";
// The standalone website is a demo of the same distributable element.
document.querySelector<HTMLDivElement>("#app")!.innerHTML =
  "<repo-history-viz></repo-history-viz>";
