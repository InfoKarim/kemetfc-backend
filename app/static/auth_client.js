(function () {
  const originalFetch = window.fetch.bind(window);
  const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);

  function cookieValue(name) {
    const prefix = `${name}=`;
    const value = document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix));

    return value ? decodeURIComponent(value.slice(prefix.length)) : "";
  }

  window.fetch = async function (input, init = {}) {
    const request = input instanceof Request ? input : null;
    const url = new URL(
      request ? request.url : String(input),
      window.location.origin
    );
    const method = String(
      init.method || (request && request.method) || "GET"
    ).toUpperCase();
    const headers = new Headers(request ? request.headers : undefined);

    new Headers(init.headers || {}).forEach((value, key) => {
      headers.set(key, value);
    });

    if (url.origin === window.location.origin && unsafeMethods.has(method)) {
      const csrfToken = cookieValue("trainingbuddy_pilot2_csrf");
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    }

    const response = await originalFetch(input, {
      ...init,
      headers,
      credentials: "same-origin",
    });

    if (response.status === 401 && window.location.pathname !== "/login") {
      const next = encodeURIComponent(
        `${window.location.pathname}${window.location.search}`
      );
      window.location.assign(`/login?next=${next}`);
    }

    return response;
  };

  window.addEventListener("DOMContentLoaded", () => {
    if (window.location.pathname === "/login") return;

    const pageFeatures = new Map([
      ["/dashboard", "dashboard"],
      ["/players-dashboard", "players"],
      ["/player-details", "players"],
      ["/add-player", "players"],
      ["/teams-dashboard", "teams"],
      ["/team-details", "teams"],
      ["/add-team", "teams"],
      ["/assessments-dashboard", "assessments"],
      ["/assessment-details", "assessments"],
      ["/add-assessment", "assessments"],
      ["/development-snapshot", "assessments"],
      ["/training-plans-dashboard", "training"],
      ["/training-plan-details", "training"],
      ["/drill-library", "training"],
      ["/videos-dashboard", "videos"],
      ["/video-analysis-details", "videos"],
      ["/add-video", "videos"],
      ["/upload-player-video", "videos"],
      ["/matches-dashboard", "matches"],
      ["/add-match", "matches"],
      ["/reports-dashboard", "reports"],
      ["/calendar-dashboard", "calendar"],
      ["/messages-page", "messaging"],
      ["/registrations-dashboard", "assessments"],
    ]);

    originalFetch("/auth/me", { credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (!data?.user) return;

        if (data.user.role !== "admin") {
          const enabled = new Set(data.user.feature_permissions || []);

          for (const link of document.querySelectorAll("a[href]")) {
            const pathname = new URL(link.href, location.origin).pathname;
            const feature = pageFeatures.get(pathname);
            if (feature && !enabled.has(feature)) link.hidden = true;
          }
        }

        if ((data.user.role === "admin" || (data.user.feature_permissions || []).includes("messaging"))) {
          originalFetch("/messages/unread-count", { credentials: "same-origin" })
            .then((response) => response.ok ? response.json() : null)
            .then((result) => {
              const badge = document.querySelector("#messaging-badge");
              if (!badge || !result) return;
              const count = result.unread_count || 0;
              badge.textContent = count > 9 ? "9+" : String(count);
              badge.hidden = count === 0;
            })
            .catch(() => {});
        }
      });
  });
})();
