// Sign in as the guest from this site, then open the dashboard.
// The session has to be created here. A cookie set while arriving from another
// site is not the one the dashboard sends on its own requests.
(function () {
  fetch("/api/v1/auth/guest/session", {
    method: "POST",
    credentials: "include",
  })
    .then(function (response) {
      if (!response.ok) throw new Error(String(response.status));
      window.location.replace("/");
    })
    .catch(function () {
      window.location.replace("/");
    });
})();
