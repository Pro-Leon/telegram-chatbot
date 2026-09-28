/* notifications.js -- creator-scoped realtime toasts for commerce and execution visibility */
(function () {
  "use strict";
  function showToast(msg, type) {
    type = type || "info";
    var t = document.getElementById("toast");
    if (!t) {
      t = document.createElement("div");
      t.id = "toast";
      t.style.cssText = "position:fixed;bottom:20px;right:20px;padding:12px 16px;border-radius:8px;color:#fff;z-index:9999;transition:opacity 0.3s;max-width:320px;font-size:14px;";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.background = type === "error" ? "#DC2626" : type === "success" ? "#059669" : type === "commerce" ? "#7C3AED" : "#1F2937";
    t.style.opacity = "1";
    clearTimeout(t._hide);
    t._hide = setTimeout(function () { t.style.opacity = "0"; }, 4000);
  }
  function initNotifications(rt) {
    if (!rt || !rt.on) return;
    rt.on("commerce.offer_created", function (e) {
      var d = e.data || {};
      showToast("PPV sent" + (d.product_id ? " #" + d.product_id : ""), "commerce");
    });
    rt.on("commerce.sale_recorded", function (e) {
      var d = e.data || {};
      var amt = d.amount_cents ? "$" + (d.amount_cents / 100).toFixed(2) : "";
      showToast("Sale made " + amt, "success");
    });
    rt.on("commerce.attribution", function (e) {
      var d = e.data || {};
      if (d.attribution_status === "attributed") showToast("Buyer attributed", "success");
    });
    rt.on("commerce.aftercare", function (e) {
      var d = e.data || {};
      showToast("Aftercare " + (d.state || ""), "info");
    });
    rt.on("commerce.funnel_changed", function (e) {
      var d = e.data || {};
      showToast("Funnel " + d.from_stage + " -> " + d.to_stage, "info");
    });
    rt.on("commerce.sale_lost", function (e) {
      showToast("Sale lost", "error");
    });
    rt.on("message.sent", function (e) {
      // Only show for auto-approved to avoid spam for operator sends
      var d = e.data || {};
      if (d.was_auto_approved) showToast("Reply sent", "info");
    });
    rt.on("message.send_failed", function (e) {
      showToast("Send failed", "error");
    });
    rt.on("ai.generation_failed", function (e) {
      showToast("Generation failed", "error");
    });
  }
  // Auto-init if RealtimeClient exists
  document.addEventListener("DOMContentLoaded", function () {
    if (window.RealtimeClient && window.rt) {
      initNotifications(window.rt);
    } else if (window.RealtimeClient) {
      var rt = new RealtimeClient();
      window.rt = rt;
      initNotifications(rt);
    }
  });
  window.initNotifications = initNotifications;
  window.showToast = showToast;
})();
