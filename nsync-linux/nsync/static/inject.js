// Added to Syncthing's own page: a status strip about Nostr discovery, and an Nsync entry in the top bar
// that opens the full panel (relays, pairing, settings) over the page.
(function () {
  if (window.__nsync) return;
  window.__nsync = true;

  var PURPLE = "#8e30eb";
  var overlay = document.createElement("div");
  overlay.style.cssText = "display:none;position:fixed;inset:0;z-index:2001;background:rgba(0,0,0,.55)";
  overlay.innerHTML = '<div style="position:absolute;inset:3% 4%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.5)">' +
    '<button id="nsync-close" style="position:absolute;right:10px;top:8px;z-index:1;border:0;background:' + PURPLE + ';color:#fff;border-radius:4px;padding:3px 12px;cursor:pointer">Close</button>' +
    '<iframe src="about:blank" style="width:100%;height:100%;border:0"></iframe></div>';
  function open(e) { if (e && e.preventDefault) e.preventDefault(); overlay.querySelector("iframe").src = "/nsync/"; overlay.style.display = "block"; }
  function close() { overlay.style.display = "none"; overlay.querySelector("iframe").src = "about:blank"; refresh(); }
  overlay.addEventListener("click", function (e) { if (e.target === overlay || e.target.id === "nsync-close") close(); });
  document.body.appendChild(overlay);

  var icon = '<img src="/nsync/logo.svg" alt="" style="width:20px;height:20px;vertical-align:text-bottom">&nbsp;Nsync';
  function addToNavbar() {
    var bar = document.querySelector("ul.navbar-right");
    if (!bar || document.getElementById("nsync-nav")) return !!bar;
    var li = document.createElement("li");
    li.id = "nsync-nav";
    li.innerHTML = '<a href="#" class="navbar-link" title="Nsync: Nostr discovery">' + icon + "</a>";
    li.firstChild.onclick = open;
    bar.insertBefore(li, bar.firstChild);
    return true;
  }

  function ago(t) {
    if (!t) return "never";
    var s = Math.max(0, Date.now() / 1000 - t);
    return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago";
  }
  function inn(t) {
    var s = t - Date.now() / 1000;
    return s <= 90 ? "within a minute" : s < 5400 ? "in " + Math.round(s / 60) + " min" : "in " + Math.round(s / 3600) + " h";
  }

  var strip = null;
  function ensureStrip() {
    if (strip && document.body.contains(strip)) return strip;
    var hosts = document.querySelectorAll(".container.content");
    var host = null;
    for (var i = 0; i < hosts.length; i++) if (hosts[i].offsetParent !== null && hosts[i].children.length > 1) host = hosts[i];
    if (!host) return null;
    strip = document.createElement("div");
    strip.id = "nsync-strip";
    strip.style.cssText = "margin:10px 0 0;padding:8px 12px;border:1px solid " + PURPLE + ";border-left-width:5px;border-radius:4px;background:rgba(142,48,235,.12);display:flex;flex-wrap:wrap;align-items:center;gap:6px 14px";
    strip.innerHTML = '<img src="/nsync/logo.svg" alt="" style="width:26px;height:26px">' +
      '<b>Nostr discovery</b><span id="nsync-text" style="flex:1 1 280px">…</span>' +
      '<button type="button" class="btn btn-default btn-sm" id="nsync-refresh"><span class="fa fa-refresh"></span> Refresh now</button>' +
      '<button type="button" class="btn btn-default btn-sm" id="nsync-details">Relays and details</button>';
    host.insertBefore(strip, host.firstChild);
    strip.querySelector("#nsync-details").onclick = open;
    strip.querySelector("#nsync-refresh").onclick = function () {
      var b = this; b.disabled = true;
      fetch("/nsync/api/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
        .then(function () { return refresh(); }).finally(function () { b.disabled = false; });
    };
    return strip;
  }

  function refresh() {
    return fetch("/nsync/api/summary").then(function (r) { return r.json(); }).then(function (s) {
      var el = ensureStrip(); if (!el) return;
      var t = el.querySelector("#nsync-text");
      if (!s.core_ready) { t.textContent = "Starting up…"; return; }
      var warn = s.relays_connected === 0;
      el.style.borderColor = warn ? "#d9534f" : PURPLE;
      if (!s.announced_at) {
        t.textContent = warn ? "Not connected to any relay: other devices cannot find this one." : "Connected to " + s.relays_connected + " of " + s.relays_total + " relays; announcing this device…";
        return;
      }
      t.innerHTML = "Your address was published to <b>" + s.relays_accepted + " of " + s.relays_total + "</b> relays " + ago(s.announced_at) +
        " so your other devices can connect here. Next update " + inn(s.next_republish_at) + " (sooner if your IP changes).";
    }).catch(function () {});
  }

  // Syncthing draws its bar and content after the page loads, and may redraw them: wait, then keep them there.
  var tries = 0;
  var timer = setInterval(function () {
    var ok = addToNavbar();
    if (ok) { refresh(); if (++tries > 4) clearInterval(timer); }
    if (!ok && tries++ > 100) clearInterval(timer);
  }, 300);
  setInterval(refresh, 5000);
  new MutationObserver(function () { addToNavbar(); ensureStrip(); }).observe(document.body, { childList: true, subtree: true });
})();
