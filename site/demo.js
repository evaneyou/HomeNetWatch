// The live-dashboard links sign in as the read-only guest on their own.
// An older /welcome/#demo address still does that. This also drives the system-layer tabs.
(function () {
  if (window.location.hash === "#demo") {
    window.location.replace("https://live.homenetwatch.com/api/v1/auth/guest");
  }

  // Interactive topology layer switcher
  var topologyShell = document.querySelector(".topology-shell");
  var inspectorPane = document.querySelector(".inspector-pane");
  var nodeTabs = document.querySelectorAll("[data-node]");
  var panels = document.querySelectorAll("[data-panel]");
  var stackedQuery = window.matchMedia("(max-width: 900px)");

  function placeInspector() {
    if (!topologyShell || !inspectorPane) return;
    var activeTab = document.querySelector(".node-btn.active") || nodeTabs[0];
    if (stackedQuery.matches && activeTab && activeTab.parentNode) {
      activeTab.insertAdjacentElement("afterend", inspectorPane);
    } else if (inspectorPane.parentNode !== topologyShell) {
      topologyShell.appendChild(inspectorPane);
    }
  }

  function selectNode(nodeKey, fromClick) {
    var clickedTab = null;
    nodeTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-node") === nodeKey;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
      if (active) clickedTab = tab;
    });
    var prevTop = clickedTab ? clickedTab.getBoundingClientRect().top : null;
    panels.forEach(function (panel) {
      var match = panel.getAttribute("data-panel") === nodeKey;
      if (match) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
    placeInspector();
    if (fromClick && stackedQuery.matches && clickedTab && inspectorPane) {
      if (prevTop !== null) {
        var newTop = clickedTab.getBoundingClientRect().top;
        if (Math.abs(newTop - prevTop) > 1) {
          window.scrollBy(0, newTop - prevTop);
        }
      }
      var tabRect = clickedTab.getBoundingClientRect();
      var paneRect = inspectorPane.getBoundingClientRect();
      if (tabRect.top < 12 || paneRect.bottom > window.innerHeight - 12) {
        var targetY = window.scrollY + tabRect.top - 16;
        window.scrollTo({ top: Math.max(0, targetY), behavior: "smooth" });
      }
    }
  }

  nodeTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var key = tab.getAttribute("data-node");
      if (key) selectNode(key, true);
    });
  });

  placeInspector();
  if (stackedQuery.addEventListener) {
    stackedQuery.addEventListener("change", placeInspector);
  }
})();
