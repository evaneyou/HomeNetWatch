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
  var nodeTabs = document.querySelectorAll("[data-node]");
  var panels = document.querySelectorAll("[data-panel]");

  function selectNode(nodeKey) {
    nodeTabs.forEach(function (tab) {
      var active = tab.getAttribute("data-node") === nodeKey;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach(function (panel) {
      var match = panel.getAttribute("data-panel") === nodeKey;
      if (match) {
        panel.removeAttribute("hidden");
      } else {
        panel.setAttribute("hidden", "");
      }
    });
  }

  nodeTabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var key = tab.getAttribute("data-node");
      if (key) selectNode(key);
    });
  });

  if (onApp && window.location.hash === "#demo") enter();
})();
