const tabButtons = document.querySelectorAll(".tab-btn");
const views = document.querySelectorAll(".view");
const tabIndicator = document.querySelector(".tab-indicator");

function moveIndicatorTo(btn) {
  if (!tabIndicator) return;
  tabIndicator.style.width = `${btn.offsetWidth}px`;
  tabIndicator.style.transform = `translateX(${btn.offsetLeft}px)`;
}

function showView(view) {
  view.hidden = false;
  view.classList.remove("view--enter");
  // Force a reflow so the animation restarts every time this view is shown.
  void view.offsetWidth;
  view.classList.add("view--enter");
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    tabButtons.forEach((b) => b.classList.remove("active"));
    views.forEach((v) => (v.hidden = true));
    btn.classList.add("active");
    moveIndicatorTo(btn);
    showView(document.getElementById(btn.dataset.view));
  });
});

const initialActive = document.querySelector(".tab-btn.active");
if (initialActive) moveIndicatorTo(initialActive);
window.addEventListener("resize", () => {
  const active = document.querySelector(".tab-btn.active");
  if (active) moveIndicatorTo(active);
});
