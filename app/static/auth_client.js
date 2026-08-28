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

  const roleLabels = {
    admin: "Administrator",
    coach: "Coach",
    reviewer: "Reviewer",
    guardian: "Guardian",
  };

  function ensureAccountWidget(user) {
    if (document.querySelector("#account-sign-out")) return;

    const style = document.createElement("style");
    style.textContent = `
      #fa-widget {
        position: fixed !important;
        right: 18px !important;
        bottom: 18px !important;
        z-index: 2147483000 !important;
        width: 216px !important;
        padding: 14px !important;
        border-radius: 14px !important;
        background: linear-gradient(180deg, #0d2543, #051428) !important;
        border: 1px solid rgba(255,255,255,.14) !important;
        box-shadow: 0 16px 40px rgba(0,0,0,.4) !important;
        font: 13px/1.4 Inter, ui-sans-serif, system-ui, -apple-system, sans-serif !important;
      }
      #fa-widget strong { display: block !important; color: #fff !important; font-size: 13px !important; }
      #fa-widget .fa-role { display: block !important; color: #8ca1bb !important; font-size: 11px !important; margin-bottom: 10px !important; }
      #fa-widget button {
        display: block !important; width: 100% !important; margin-top: 8px !important; padding: 9px 12px !important;
        border: 1px solid rgba(255,255,255,.16) !important; border-radius: 8px !important;
        background: rgba(255,255,255,.06) !important; color: #dce7f5 !important;
        font: inherit !important; font-weight: 700 !important; cursor: pointer !important; text-align: center !important;
        min-height: 0 !important;
      }
      #fa-widget button:hover { background: rgba(255,255,255,.12) !important; }
      #fa-widget [hidden] { display: none !important; }
      #fa-widget button:disabled { opacity: .6 !important; cursor: wait !important; }
      #fa-sign-out { color: #ff9b9b !important; border-color: rgba(255,155,155,.32) !important; background: rgba(255,155,155,.08) !important; }
      #fa-sign-out:hover { background: rgba(255,155,155,.16) !important; }
      #fa-password-form { display: none !important; margin-top: 8px !important; }
      #fa-password-form.open { display: grid !important; gap: 8px !important; }
      #fa-password-form input {
        width: 100% !important; padding: 8px 10px !important; border: 1px solid rgba(255,255,255,.2) !important; border-radius: 7px !important;
        background: rgba(255,255,255,.07) !important; color: #fff !important; font: inherit !important; font-size: 12px !important;
      }
      #fa-password-form input::placeholder { color: #8ca1bb !important; }
      #fa-password-status { min-height: 14px !important; margin: 2px 0 0 !important; font-size: 11px !important; color: #8ca1bb !important; }
      #fa-password-status.error { color: #ff9b9b !important; }
      #fa-password-status.success { color: #7cdb8f !important; }
      #fa-widget .fa-actions { display: flex !important; gap: 6px !important; }
      #fa-widget .fa-actions button { margin-top: 0 !important; flex: 1 !important; padding: 8px !important; font-size: 11px !important; }
    `;
    document.head.appendChild(style);

    const widget = document.createElement("div");
    widget.id = "fa-widget";

    const name = document.createElement("strong");
    name.textContent = user.username || "";
    const role = document.createElement("span");
    role.className = "fa-role";
    role.textContent = roleLabels[user.role] || user.role || "";

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.textContent = "Change password";

    const form = document.createElement("form");
    form.id = "fa-password-form";
    form.innerHTML = `
      <input type="password" id="fa-current-password" placeholder="Current password" autocomplete="current-password" required>
      <input type="password" id="fa-new-password" placeholder="New password (12+ characters)" autocomplete="new-password" minlength="12" required>
      <p id="fa-password-status" role="status"></p>
      <div class="fa-actions">
        <button type="button" id="fa-cancel-password">Cancel</button>
        <button type="submit">Save</button>
      </div>
    `;

    const signOutBtn = document.createElement("button");
    signOutBtn.type = "button";
    signOutBtn.id = "fa-sign-out";
    signOutBtn.textContent = "Sign out";

    widget.append(name, role, toggleBtn, form, signOutBtn);
    document.body.appendChild(widget);

    const status = form.querySelector("#fa-password-status");

    function resetForm() {
      form.classList.remove("open");
      toggleBtn.hidden = false;
      form.reset();
      status.textContent = "";
      status.className = "";
    }

    toggleBtn.addEventListener("click", () => {
      toggleBtn.hidden = true;
      form.classList.add("open");
      form.querySelector("#fa-current-password").focus();
    });

    form.querySelector("#fa-cancel-password").addEventListener("click", resetForm);

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = form.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      status.className = "";
      status.textContent = "Saving...";

      try {
        const response = await fetch("/auth/me/password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            current_password: form.querySelector("#fa-current-password").value,
            new_password: form.querySelector("#fa-new-password").value,
          }),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(result.detail || "Could not change password.");
        }

        status.className = "success";
        status.textContent = "Password changed. Signing you out...";
        setTimeout(() => window.location.assign("/login"), 1200);
      } catch (error) {
        status.className = "error";
        status.textContent = error.message;
        submitButton.disabled = false;
      }
    });

    signOutBtn.addEventListener("click", async (event) => {
      event.target.disabled = true;
      await fetch("/auth/logout", { method: "POST" });
      window.location.assign("/login");
    });
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

        ensureAccountWidget(data.user);

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
