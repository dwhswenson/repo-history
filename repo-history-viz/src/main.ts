import "./register.js";
// The standalone website is a demo of the same distributable element.
document.querySelector<HTMLDivElement>("#app")!.innerHTML =
  '<repo-history-viz measures="github-activities commits contributors total-contributors balance"></repo-history-viz>';
