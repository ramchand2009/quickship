(function () {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  const POLL_URL = "/orders/notifications/poll/";
  const LAST_SEEN_KEY = "mathukai:lastWooOrderSeenAt";
  const PROMPT_DISMISSED_KEY = "mathukai:orderNotificationPromptDismissed";
  const POLL_INTERVAL_MS = 30000;

  function canUseNotifications() {
    return "Notification" in window && "serviceWorker" in navigator;
  }

  function createNotificationPrompt() {
    if (!canUseNotifications() || Notification.permission !== "default") {
      return;
    }
    if (window.localStorage.getItem(PROMPT_DISMISSED_KEY) === "1") {
      return;
    }
    if (document.getElementById("pwaOrderNotificationPrompt")) {
      return;
    }

    const prompt = document.createElement("div");
    prompt.id = "pwaOrderNotificationPrompt";
    prompt.setAttribute("role", "status");
    prompt.style.cssText = [
      "position:fixed",
      "left:16px",
      "right:16px",
      "bottom:16px",
      "z-index:1080",
      "display:flex",
      "align-items:center",
      "justify-content:space-between",
      "gap:12px",
      "padding:12px 14px",
      "border-radius:8px",
      "background:#0f172a",
      "color:#fff",
      "box-shadow:0 12px 30px rgba(15,23,42,.25)",
      "font-size:14px",
      "line-height:1.35"
    ].join(";");

    const text = document.createElement("div");
    text.textContent = "Enable new order notifications for Quickship.";

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;align-items:center;flex-shrink:0";

    const enableButton = document.createElement("button");
    enableButton.type = "button";
    enableButton.textContent = "Enable";
    enableButton.style.cssText = "border:0;border-radius:6px;padding:7px 10px;background:#38bdf8;color:#082f49;font-weight:700";

    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Dismiss notification prompt");
    closeButton.textContent = "×";
    closeButton.style.cssText = "border:0;border-radius:6px;padding:7px 10px;background:rgba(255,255,255,.12);color:#fff;font-weight:700";

    enableButton.addEventListener("click", function () {
      Notification.requestPermission().then(function () {
        prompt.remove();
        startOrderNotificationPolling();
      });
    });
    closeButton.addEventListener("click", function () {
      window.localStorage.setItem(PROMPT_DISMISSED_KEY, "1");
      prompt.remove();
    });

    actions.appendChild(enableButton);
    actions.appendChild(closeButton);
    prompt.appendChild(text);
    prompt.appendChild(actions);
    document.body.appendChild(prompt);
  }

  function showOrderNotification(order) {
    if (!canUseNotifications() || Notification.permission !== "granted") {
      return;
    }
    navigator.serviceWorker.ready.then(function (registration) {
      const orderId = order.order_id || "New order";
      const customer = order.customer_name || "WooCommerce customer";
      registration.showNotification("New WooCommerce order", {
        body: orderId + " · " + customer + " · Rs " + (order.total || "0"),
        tag: "woocommerce-order-" + String(order.id || orderId),
        renotify: true,
        icon: "/static/pwa/icon-192.png?v=20260512-1",
        badge: "/static/pwa/icon-192.png?v=20260512-1",
        data: {
          url: order.url || "/orders/management/"
        }
      });
    }).catch(function (error) {
      console.warn("Unable to show order notification.", error);
    });
  }

  function pollOrders() {
    const since = window.localStorage.getItem(LAST_SEEN_KEY) || "";
    const url = since ? POLL_URL + "?since=" + encodeURIComponent(since) : POLL_URL;
    fetch(url, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Notification poll failed with HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (payload) {
        if (!payload || !payload.ok) {
          return;
        }
        const orders = Array.isArray(payload.orders) ? payload.orders : [];
        orders.forEach(showOrderNotification);
        if (payload.latest_seen_at) {
          window.localStorage.setItem(LAST_SEEN_KEY, payload.latest_seen_at);
        }
      })
      .catch(function () {
        // Polling is opportunistic; failed checks should not interrupt operators.
      });
  }

  function startOrderNotificationPolling() {
    if (!canUseNotifications() || Notification.permission !== "granted") {
      return;
    }
    pollOrders();
    window.setInterval(pollOrders, POLL_INTERVAL_MS);
  }

  window.addEventListener("load", function () {
    navigator.serviceWorker.register("/service-worker.js").then(function () {
      createNotificationPrompt();
      startOrderNotificationPolling();
    }).catch(function (error) {
      console.warn("PWA service worker registration failed.", error);
    });
  });
})();
