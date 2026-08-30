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
      <button type="button" id="account-sign-out">Sign out</button>
    `;
    aside.appendChild(panel);

    renderAccountAvatar(user);
    document.querySelector("#account-name").textContent = user.username || "";
    document.querySelector("#account-role").textContent = roleLabels[user.role] || user.role || "";

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

        if (data.user.role === "admin") {
          const usersLink = document.querySelector("#admin-users-link");
          if (usersLink) usersLink.hidden = false;
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
