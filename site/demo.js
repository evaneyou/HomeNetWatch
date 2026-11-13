// The live-dashboard links sign in as the read-only guest on their own.
// An older /welcome/#demo address still does that. This also drives the system-layer tabs
// and the pointer effects on the page background.
(function () {
  if (window.location.hash === "#demo") {
    window.location.replace("https://live.homenetwatch.com/welcome/enter.html");
  }

  // System-layer tabs
  var tabs = Array.prototype.slice.call(document.querySelectorAll("[data-node]"));
  var panels = document.querySelectorAll("[data-panel]");

  function selectNode(key, focus) {
    tabs.forEach(function (tab) {
      var active = tab.getAttribute("data-node") === key;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.setAttribute("tabindex", active ? "0" : "-1");
      if (active && focus) tab.focus();
    });
    panels.forEach(function (panel) {
      if (panel.getAttribute("data-panel") === key) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
  }

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () {
      selectNode(tab.getAttribute("data-node"), false);
    });
    tab.addEventListener("keydown", function (event) {
      var next = null;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
      if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index + tabs.length - 1) % tabs.length];
      if (event.key === "Home") next = tabs[0];
      if (event.key === "End") next = tabs[tabs.length - 1];
      if (next) {
        event.preventDefault();
        selectNode(next.getAttribute("data-node"), true);
      }
    });
  });

  // The radar and the glow follow the pointer.
  var page = document.querySelector(".page");
  var pending = null;
  if (page) {
    page.addEventListener("pointermove", function (event) {
      if (event.pointerType === "touch") return;
      var first = pending === null;
      pending = event;
      if (!first) return;
      window.requestAnimationFrame(function () {
        var rect = page.getBoundingClientRect();
        var x = pending.clientX - rect.left;
        var y = pending.clientY - rect.top;
        pending = null;
        page.style.setProperty("--px", x + "px");
        page.style.setProperty("--py", y + "px");
        page.style.setProperty("--mx", (x / rect.width - 0.5).toFixed(3));
        page.style.setProperty("--my", (Math.min(y, 900) / 900 - 0.5).toFixed(3));
      });
    });
  }

  // A click on the hero sends a ping ring out from that point.
  var hero = document.querySelector(".hero");
  if (hero) {
    hero.addEventListener("click", function (event) {
      if (event.target.closest("a, button")) return;
      var rect = hero.getBoundingClientRect();
      var ring = document.createElement("span");
      ring.className = "ripple";
      ring.setAttribute("aria-hidden", "true");
      ring.style.left = event.clientX - rect.left + "px";
      ring.style.top = event.clientY - rect.top + "px";
      ring.addEventListener("animationend", function () {
        ring.remove();
      });
      hero.appendChild(ring);
    });
  }
})();
