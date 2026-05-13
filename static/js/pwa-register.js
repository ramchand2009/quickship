(function () {
  if (!("serviceWorker" in navigator)) {
    return;
  }

  const POLL_URL = "/orders/notifications/poll/";
  const PUSH_CONFIG_URL = "/orders/notifications/push/config/";
  const PUSH_SUBSCRIBE_URL = "/orders/notifications/push/subscribe/";
  const LAST_SEEN_KEY = "mathukai:lastWooOrderSeenAt";
  const PROMPT_DISMISSED_KEY = "mathukai:orderNotificationPromptDismissed";
  const POLL_INTERVAL_MS = 30000;
  let notificationAudioContext = null;
  let preferredSpeechVoice = null;

  function canUseNotifications() {
    return "Notification" in window && "serviceWorker" in navigator;
  }

  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (let index = 0; index < cookies.length; index += 1) {
      const cookie = cookies[index].trim();
      if (cookie.substring(0, name.length + 1) === name + "=") {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return "";
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let index = 0; index < rawData.length; index += 1) {
      outputArray[index] = rawData.charCodeAt(index);
    }
    return outputArray;
  }

  function prepareNotificationSound() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      return;
    }
    if (!notificationAudioContext) {
      notificationAudioContext = new AudioContextClass();
    }
    if (notificationAudioContext.state === "suspended") {
      notificationAudioContext.resume().catch(function () {});
    }
  }

  function playNotificationSound() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      return;
    }
    if (!notificationAudioContext) {
      notificationAudioContext = new AudioContextClass();
    }
    if (notificationAudioContext.state === "suspended") {
      notificationAudioContext.resume().catch(function () {});
    }
    try {
      const now = notificationAudioContext.currentTime;
      const gain = notificationAudioContext.createGain();
      const firstTone = notificationAudioContext.createOscillator();
      const secondTone = notificationAudioContext.createOscillator();

      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.16, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.55);
      gain.connect(notificationAudioContext.destination);

      firstTone.type = "sine";
      firstTone.frequency.setValueAtTime(880, now);
      firstTone.connect(gain);
      firstTone.start(now);
      firstTone.stop(now + 0.18);

      secondTone.type = "sine";
      secondTone.frequency.setValueAtTime(1175, now + 0.2);
      secondTone.connect(gain);
      secondTone.start(now + 0.2);
      secondTone.stop(now + 0.5);
    } catch (error) {
      console.warn("Unable to play order notification sound.", error);
    }
  }

  function loadPreferredSpeechVoice() {
    if (!("speechSynthesis" in window)) {
      return null;
    }
    const voices = window.speechSynthesis.getVoices();
    preferredSpeechVoice = voices.find(function (voice) {
      return voice.lang && voice.lang.toLowerCase().indexOf("en") === 0 && /female|samantha|zira|susan|karen|moira|tessa/i.test(voice.name);
    }) || voices.find(function (voice) {
      return voice.lang && voice.lang.toLowerCase().indexOf("en") === 0;
    }) || voices[0] || null;
    return preferredSpeechVoice;
  }

  function speakOrderNotification() {
    if (!("speechSynthesis" in window) || !("SpeechSynthesisUtterance" in window)) {
      return;
    }
    try {
      const utterance = new SpeechSynthesisUtterance("You have received a new order");
      utterance.voice = preferredSpeechVoice || loadPreferredSpeechVoice();
      utterance.lang = utterance.voice && utterance.voice.lang ? utterance.voice.lang : "en-IN";
      utterance.rate = 0.95;
      utterance.pitch = 1.1;
      utterance.volume = 1;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    } catch (error) {
      console.warn("Unable to speak order notification.", error);
    }
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
      prepareNotificationSound();
      loadPreferredSpeechVoice();
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
      playNotificationSound();
      speakOrderNotification();
      registration.showNotification("New WooCommerce order", {
        body: orderId + " · " + customer + " · Rs " + (order.total || "0"),
        tag: "woocommerce-order-" + String(order.id || orderId),
        renotify: true,
        icon: "/static/pwa/icon-192.png?v=20260513-1",
        badge: "/static/pwa/icon-192.png?v=20260513-1",
        data: {
          url: order.url || "/orders/management/"
        }
      });
    }).catch(function (error) {
      console.warn("Unable to show order notification.", error);
    });
  }

  function subscribeToPushNotifications(registration) {
    if (!registration || !("PushManager" in window) || Notification.permission !== "granted") {
      return;
    }
    fetch(PUSH_CONFIG_URL, {
      credentials: "same-origin",
      headers: { "Accept": "application/json" }
    })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("Push config failed with HTTP " + response.status);
        }
        return response.json();
      })
      .then(function (config) {
        if (!config || !config.enabled || !config.public_key) {
          return null;
        }
        return registration.pushManager.getSubscription().then(function (existingSubscription) {
          if (existingSubscription) {
            return existingSubscription;
          }
          return registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlBase64ToUint8Array(config.public_key)
          });
        });
      })
      .then(function (subscription) {
        if (!subscription) {
          return;
        }
        return fetch(PUSH_SUBSCRIBE_URL, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken")
          },
          body: JSON.stringify(subscription)
        });
      })
      .catch(function (error) {
        console.warn("Unable to register push notifications.", error);
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
    navigator.serviceWorker.ready.then(subscribeToPushNotifications);
    pollOrders();
    window.setInterval(pollOrders, POLL_INTERVAL_MS);
  }

  window.addEventListener("load", function () {
    if ("speechSynthesis" in window) {
      loadPreferredSpeechVoice();
      window.speechSynthesis.onvoiceschanged = loadPreferredSpeechVoice;
    }
    navigator.serviceWorker.register("/service-worker.js").then(function () {
      createNotificationPrompt();
      startOrderNotificationPolling();
    }).catch(function (error) {
      console.warn("PWA service worker registration failed.", error);
    });
  });
})();
