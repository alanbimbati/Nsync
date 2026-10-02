// Added to Syncthing's own page: an "Nsync" entry in its top bar that opens the Nsync panel over it.
(function () {
  if (window.__nsync) return;
  window.__nsync = true;

  var overlay = document.createElement("div");
  overlay.style.cssText = "display:none;position:fixed;inset:0;z-index:2001;background:rgba(0,0,0,.5)";
  overlay.innerHTML = '<div style="position:absolute;inset:4% 6%;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.5)">' +
    '<button id="nsync-close" style="position:absolute;right:8px;top:6px;z-index:1;border:0;background:#8e30eb;color:#fff;border-radius:4px;padding:2px 10px;cursor:pointer">Close</button>' +
    '<iframe src="about:blank" style="width:100%;height:100%;border:0"></iframe></div>';
  function open(e) { if (e) e.preventDefault(); overlay.querySelector("iframe").src = "/nsync/"; overlay.style.display = "block"; }
  function close() { overlay.style.display = "none"; overlay.querySelector("iframe").src = "about:blank"; }
  overlay.addEventListener("click", function (e) { if (e.target === overlay || e.target.id === "nsync-close") close(); });
  document.body.appendChild(overlay);

  var icon = '<img src="/nsync/logo.svg" alt="" style="width:20px;height:20px;vertical-align:text-bottom">&nbsp;Nsync';

  function addToNavbar() {
    var bar = document.querySelector("ul.navbar-right");
    if (!bar || document.getElementById("nsync-nav")) return !!bar;
    var li = document.createElement("li");
    li.id = "nsync-nav";
    li.innerHTML = '<a href="#" class="navbar-link" title="Nsync">' + icon + "</a>";
    li.firstChild.onclick = open;
    bar.insertBefore(li, bar.firstChild);
    return true;
  }

  // Syncthing draws its bar after the page loads, and may redraw it: wait for it and keep the entry there.
  var tries = 0;
  var timer = setInterval(function () {
    if (addToNavbar()) { clearInterval(timer); new MutationObserver(addToNavbar).observe(document.body, { childList: true, subtree: true }); }
    else if (++tries > 100) { // no bar found: a floating button, in the corner away from Syncthing's own buttons
      clearInterval(timer);
      var b = document.createElement("button");
      b.innerHTML = icon;
      b.style.cssText = "position:fixed;left:16px;bottom:16px;z-index:2000;padding:6px 12px;border:0;border-radius:24px;background:#fff;color:#8e30eb;box-shadow:0 2px 8px rgba(0,0,0,.35);cursor:pointer";
      b.onclick = open;
      document.body.appendChild(b);
    }
  }, 300);
})();
