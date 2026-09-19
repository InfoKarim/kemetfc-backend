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

  function renderAccountAvatar(user) {
    const avatar = document.querySelector("#account-avatar");
    const removeBtn = document.querySelector("#account-avatar-remove");
    if (!avatar || !removeBtn) return;

    if (user.avatar_url) {
      avatar.replaceChildren();
      const img = document.createElement("img");
      img.src = `${user.avatar_url}?t=${Date.now()}`;
      img.alt = "";
      avatar.append(img);
      removeBtn.hidden = false;
    } else {
      avatar.replaceChildren();
      avatar.textContent = (user.username || "?").slice(0, 2).toUpperCase();
      removeBtn.hidden = true;
    }
  }

  function ensureAccountWidget(user) {
    if (document.querySelector("#account-panel")) return;

    const aside = document.querySelector("aside");
    if (!aside) return;

    aside.style.display = "flex";
    aside.style.flexDirection = "column";
    aside.style.overflowY = "auto";

    const style = document.createElement("style");
    style.textContent = `
      #account-panel {
        margin-top: auto !important;
        padding-top: 16px !important;
        border-top: 1px solid rgba(255, 255, 255, .12) !important;
      }
      #account-identity { display: flex !important; align-items: center !important; gap: 10px !important; padding: 4px 10px 12px !important; }
      #account-avatar-btn {
        position: relative !important; flex: 0 0 34px !important; width: 34px !important; height: 34px !important;
        padding: 0 !important; border: 0 !important; border-radius: 50% !important;
        background: none !important; box-shadow: none !important; cursor: pointer !important;
      }
      #account-avatar {
        display: flex !important; width: 34px !important; height: 34px !important; align-items: center !important; justify-content: center !important;
        border-radius: 50% !important; overflow: hidden !important;
        background: white !important; color: #0b2036 !important;
        font-weight: 800 !important; font-size: 12px !important;
      }
      #account-avatar img { width: 100% !important; height: 100% !important; object-fit: cover !important; }
      #account-avatar-btn::after {
        content: "\\270E"; position: absolute !important; right: -2px !important; bottom: -2px !important;
        display: grid !important; width: 15px !important; height: 15px !important; place-items: center !important;
        border-radius: 50% !important; background: #0866f5 !important; color: white !important; font-size: 9px !important;
      }
      #account-identity strong { display: block !important; color: white !important; font-size: 13px !important; }
      #account-role { display: block !important; color: #8ca1bb !important; font-size: 11px !important; }
      #account-panel > #account-avatar-remove {
        display: block !important; margin: -6px 0 8px 10px !important; padding: 0 !important; border: 0 !important;
        background: none !important; box-shadow: none !important;
        color: #8ca1bb !important; font-size: 11px !important; font-weight: 700 !important; text-decoration: underline !important;
        text-align: left !important; cursor: pointer !important; width: auto !important;
      }
      #account-panel > button {
        display: block !important; width: 100% !important; margin-top: 8px !important; padding: 10px 14px !important;
        border: 1px solid rgba(255, 255, 255, .14) !important; border-radius: 9px !important;
        background: rgba(255, 255, 255, .06) !important;
        color: #dce7f5 !important; box-shadow: none !important;
        font: inherit !important; font-size: 13px !important; font-weight: 700 !important; text-align: center !important; cursor: pointer !important;
      }
      #account-panel > button:hover {
        background: rgba(255, 255, 255, .12) !important;
        border-color: rgba(255, 255, 255, .24) !important;
      }
      #account-sign-out {
        color: #ff9b9b !important;
        border-color: rgba(255, 155, 155, .3) !important;
        background: rgba(255, 155, 155, .08) !important;
      }
      #account-sign-out:hover { background: rgba(255, 155, 155, .16) !important; }
      #account-password-form { display: grid !important; gap: 8px !important; padding: 6px 10px 4px !important; }
      #account-panel [hidden] { display: none !important; }
      #account-password-form input {
        padding: 9px 10px !important; border: 1px solid rgba(255, 255, 255, .18) !important; border-radius: 8px !important;
        background: rgba(255, 255, 255, .06) !important; color: white !important; font: inherit !important; font-size: 13px !important;
      }
      #account-password-form input::placeholder { color: #8ca1bb !important; }
      .account-password-actions { display: flex !important; gap: 8px !important; justify-content: flex-end !important; }
      .account-password-actions button {
        padding: 8px 14px !important; border: 0 !important; border-radius: 8px !important; font: inherit !important;
        font-weight: 700 !important; cursor: pointer !important; font-size: 13px !important; box-shadow: none !important;
      }
      #cancel-account-password { background: rgba(255, 255, 255, .1) !important; color: white !important; }
      #account-password-form button[type="submit"] { background: #0866f5 !important; color: white !important; }
      #account-password-status { font-size: 12px !important; min-height: 16px !important; color: #8ca1bb !important; }
      #account-password-status.error { color: #ff9b9b !important; }
      #account-password-status.success { color: #7cdb20 !important; }
      .account-apps-label {
        margin: 10px 0 4px 10px !important; padding: 0 !important;
        color: #8ca1bb !important; font-size: 11px !important; font-weight: 700 !important;
        letter-spacing: .06em !important; text-transform: uppercase !important;
      }
      #account-panel a.account-app-link {
        display: flex !important; align-items: center; justify-content: space-between; gap: 8px;
        width: 100% !important; margin-top: 8px !important; padding: 10px 14px !important; box-sizing: border-box;
        border: 1px solid rgba(255, 255, 255, .14) !important; border-radius: 9px !important;
        background: rgba(255, 255, 255, .06) !important;
        color: #dce7f5 !important; text-decoration: none !important;
        font: inherit !important; font-size: 13px !important; font-weight: 700 !important; text-align: left !important;
      }
      #account-panel a.account-app-link:hover { background: rgba(255, 255, 255, .12) !important; }
      #account-panel a.account-app-link .account-app-current {
        color: #7cdb20 !important; font-size: 11px !important; font-weight: 800 !important;
      }
    `;
    document.head.appendChild(style);

    const panel = document.createElement("div");
    panel.id = "account-panel";
    panel.innerHTML = `
      <div id="account-identity">
        <button type="button" id="account-avatar-btn" title="Change profile picture">
          <span id="account-avatar"></span>
        </button>
        <input type="file" id="account-avatar-input" accept="image/png,image/jpeg,image/webp" hidden>
        <div>
          <strong id="account-name"></strong>
          <span id="account-role"></span>
        </div>
      </div>
      <button type="button" id="account-avatar-remove" hidden>Remove photo</button>
      <p id="account-avatar-status" style="margin:-4px 0 8px 10px; font-size:11px; color:#8ca1bb;"></p>
      <button type="button" id="open-account-password">Change password</button>
      <form id="account-password-form" hidden>
        <input type="password" id="account-current-password" placeholder="Current password" autocomplete="current-password" required>
        <input type="password" id="account-new-password" placeholder="New password (12+ characters)" autocomplete="new-password" minlength="12" required>
        <p id="account-password-status" role="status"></p>
        <div class="account-password-actions">
          <button type="button" id="cancel-account-password">Cancel</button>
          <button type="submit">Save</button>
        </div>
      </form>
      <div id="account-apps" hidden>
        <p class="account-apps-label">My apps</p>
      </div>
      <button type="button" id="account-sign-out">Sign out</button>
    `;
    aside.appendChild(panel);

    renderAccountAvatar(user);
    document.querySelector("#account-name").textContent = user.username || "";
    document.querySelector("#account-role").textContent = roleLabels[user.role] || user.role || "";

    if (Array.isArray(user.my_apps) && user.my_apps.length) {
      const appsContainer = document.querySelector("#account-apps");
      for (const app of user.my_apps) {
        const link = document.createElement("a");
        link.className = "account-app-link";
        link.href = app.url;
        if (app.external) {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        }
        const label = document.createElement("span");
        label.textContent = app.name;
        link.append(label);
        if (!app.external) {
          const current = document.createElement("span");
          current.className = "account-app-current";
          current.textContent = "Current";
          link.append(current);
        }
        link.title = app.description || "";
        appsContainer.append(link);
      }
      appsContainer.hidden = false;
    }

    const avatarInput = document.querySelector("#account-avatar-input");
    const avatarStatus = document.querySelector("#account-avatar-status");

    document.querySelector("#account-avatar-btn").addEventListener("click", () => {
      avatarInput.click();
    });

    avatarInput.addEventListener("change", async () => {
      const file = avatarInput.files[0];
      if (!file) return;

      avatarStatus.textContent = "Uploading...";
      const formData = new FormData();
      formData.append("avatar", file);

      try {
        const response = await fetch("/auth/me/avatar", {
          method: "POST",
          body: formData,
        });
        const result = await response.json();

        if (!response.ok) throw new Error(result.detail || "Could not upload photo.");

        renderAccountAvatar({ ...user, avatar_url: result.avatar_url });
        avatarStatus.textContent = "";
      } catch (error) {
        avatarStatus.textContent = error.message;
      } finally {
        avatarInput.value = "";
      }
    });

    document.querySelector("#account-avatar-remove").addEventListener("click", async () => {
      avatarStatus.textContent = "Removing...";
      await fetch("/auth/me/avatar", { method: "DELETE" });
      renderAccountAvatar({ ...user, avatar_url: null });
      avatarStatus.textContent = "";
    });

    const openPasswordBtn = document.querySelector("#open-account-password");
    const passwordForm = document.querySelector("#account-password-form");
    const passwordStatus = document.querySelector("#account-password-status");

    function resetPasswordForm() {
      passwordForm.hidden = true;
      openPasswordBtn.hidden = false;
      passwordForm.reset();
      passwordStatus.textContent = "";
      passwordStatus.className = "";
    }

    openPasswordBtn.addEventListener("click", () => {
      openPasswordBtn.hidden = true;
      passwordForm.hidden = false;
      document.querySelector("#account-current-password").focus();
    });

    document.querySelector("#cancel-account-password")
      .addEventListener("click", resetPasswordForm);

    passwordForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitButton = passwordForm.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      passwordStatus.className = "";
      passwordStatus.textContent = "Saving...";

      try {
        const response = await fetch("/auth/me/password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            current_password: document.querySelector("#account-current-password").value,
            new_password: document.querySelector("#account-new-password").value,
          }),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(result.detail || "Could not change password.");
        }

        passwordStatus.className = "success";
        passwordStatus.textContent = "Password changed. Signing you out...";
        setTimeout(() => window.location.assign("/login"), 1200);
      } catch (error) {
        passwordStatus.className = "error";
        passwordStatus.textContent = error.message;
        submitButton.disabled = false;
      }
    });

    document.querySelector("#account-sign-out").addEventListener("click", async (event) => {
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

  // Feather-style stroke icons, keyed by the nav link's pathname — the
  // sidebar `<nav>` markup itself is duplicated across ~26 static pages
  // with no shared template, so icons are injected here once rather
  // than hand-edited into every file.
  const NAV_ICON_PATHS = {
    "/dashboard": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    "/players-dashboard": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "/teams-dashboard": '<path d="M12 2l8 4v6c0 5-3.5 9-8 10-4.5-1-8-5-8-10V6z"/>',
    "/assessments-dashboard": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 12l3 3 5-6"/>',
    "/training-plans-dashboard": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "/drill-library": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "/videos-dashboard": '<circle cx="12" cy="12" r="9"/><polygon points="10 8 16 12 10 16 10 8"/>',
    "/matches-dashboard": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/>',
    "/reports-dashboard": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "/calendar-dashboard": '<rect x="3" y="4" width="18" height="17" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "/admin/users": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "/registrations-dashboard": '<path d="M15 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><line x1="18" y1="8" x2="18" y2="14"/><line x1="15" y1="11" x2="21" y2="11"/>',
    "/payment-control": '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    "/payment-settings": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "/billing": '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
    "/messages-page": '<path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"/><polyline points="22 6 12 13 2 6"/>',
    "/ml-dataset-registry": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>',
  };
  const NAV_ICON_FALLBACK = '<circle cx="12" cy="12" r="3"/>';

  function iconSvg(pathname) {
    const inner = NAV_ICON_PATHS[pathname] || NAV_ICON_FALLBACK;
    return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  }

  /// Adds a leading icon to every sidebar nav link and a "MAIN" section
  /// label above the list — purely cosmetic, runs once per page load,
  /// safe to run before /auth/me resolves since it doesn't depend on
  /// the signed-in user.
  function enhanceSidebarNav() {
    const nav = document.querySelector(".layout aside nav");
    if (!nav || nav.dataset.enhanced) return;
    nav.dataset.enhanced = "true";

    for (const link of nav.querySelectorAll("a[href]")) {
      if (link.querySelector(".nav-icon")) continue;
      const pathname = new URL(link.href, location.origin).pathname;
      const icon = document.createElement("span");
      icon.className = "nav-icon";
      icon.innerHTML = iconSvg(pathname);
      link.prepend(icon);
    }

    const label = document.createElement("div");
    label.className = "nav-group-label";
    label.textContent = "Main";
    nav.prepend(label);
  }

  window.addEventListener("DOMContentLoaded", () => {
    if (window.location.pathname === "/login") return;

    enhanceSidebarNav();

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
      ["/tracking-analysis", "assessments"],
    ]);

    originalFetch("/auth/me", { credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : null)
      .then((data) => {
        if (!data?.user) return;

        ensureAccountWidget(data.user);

        if (data.user.role === "admin") {
          const usersLink = document.querySelector("#admin-users-link");
          if (usersLink) usersLink.hidden = false;

          const paymentControlLink = document.querySelector("#payment-control-link");
          if (paymentControlLink) paymentControlLink.hidden = false;

          const paymentSettingsLink = document.querySelector("#payment-settings-link");
          if (paymentSettingsLink) paymentSettingsLink.hidden = false;
        }

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
