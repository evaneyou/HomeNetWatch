// One click into the live demo: sign in as the read-only guest, then open the dashboard.
// Also drives the interactive 5-layer topology tabs on the welcome page.
(function () {
  var APP = "https://live.homenetwatch.com";
  var buttons = document.querySelectorAll("[data-demo]");
  var status = document.querySelector("[data-demo-status]");
  var onApp = window.location.origin === APP;

  function say(text, isError) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function enter(event) {
    if (event) event.preventDefault();
    if (!onApp) {
      window.location.assign(APP + "/welcome/#demo");
      return;
    }
    buttons.forEach(function (button) {
      button.setAttribute("aria-busy", "true");
    });
    say("Signing you in as a guest...", false);
    fetch("/api/v1/auth/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "guest", password: "guest" }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("login " + response.status);
        window.location.assign("/");
      })
      .catch(function () {
        buttons.forEach(function (button) {
          button.removeAttribute("aria-busy");
        });
        say(
          "Sign-in did not complete; opening login (username guest, password guest)...",
          true
        );
        window.setTimeout(function () {
          window.location.assign("/");
        }, 2500);
      });
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", enter);
  });

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

  if (onApp && window.location.hash === "#demo") enter();
})();
