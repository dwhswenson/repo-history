import "./register.js";
// The standalone website is a demo of the same distributable element.
document.querySelector<HTMLDivElement>("#app")!.innerHTML =
  '<repo-history-viz metrics="github-activities commits contributors-by-type total-contributors balance"></repo-history-viz>';
