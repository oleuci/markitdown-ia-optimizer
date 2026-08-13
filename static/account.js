(function () {
  "use strict";
  // charset sans caractères ambigus (0/O, 1/l/I)
  var U = "ABCDEFGHJKLMNPQRSTUVWXYZ", L = "abcdefghijkmnpqrstuvwxyz",
      D = "23456789", S = "!@#$%^&*-_=+?";
  function pick(set) {
    var b = new Uint32Array(1); crypto.getRandomValues(b);
    return set[b[0] % set.length];
  }
  function gen(len) {
    var all = U + L + D + S, out = [];
    for (var i = 0; i < len; i++) out.push(pick(all));
    // garantir au moins un de chaque classe
    var req = [U, L, D, S], pos = new Uint32Array(4); crypto.getRandomValues(pos);
    for (var j = 0; j < 4; j++) out[pos[j] % len] = pick(req[j]);
    return out.join("");
  }
  var n1 = document.getElementById("new1"), n2 = document.getElementById("new2"),
      btn = document.getElementById("genpw"), out = document.getElementById("genout"),
      val = document.getElementById("genval"), copy = document.getElementById("gencopy"),
      show = document.getElementById("showpw");
  if (btn) btn.addEventListener("click", function () {
    var pw = gen(20);
    n1.value = pw; n2.value = pw; val.textContent = pw; out.hidden = false;
    n1.type = n2.type = "text"; if (show) show.checked = true;
  });
  if (copy) copy.addEventListener("click", function () {
    navigator.clipboard.writeText(val.textContent || "").then(function () {
      copy.textContent = "Copié ✓";
    });
  });
  if (show) show.addEventListener("change", function () {
    var t = show.checked ? "text" : "password"; n1.type = t; n2.type = t;
  });
})();
