(function () {
  "use strict";
  var MAX_FILES = 20;
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var drop = document.getElementById("drop");
  var input = document.getElementById("fileInput");
  var go = document.getElementById("go");
  var clearQueueBtn = document.getElementById("clearQueue");
  var statusEl = document.getElementById("status");
  var queueEl = document.getElementById("queue");
  var results = document.getElementById("results");
  var resultsBar = document.getElementById("resultsBar");
  var resultsCount = document.getElementById("resultsCount");
  var clearResultsBtn = document.getElementById("clearResults");
  var modeSel = document.getElementById("mode");
  var LEVELS = JSON.parse(results.dataset.levels || "[]");
  var LLM_ON = results.dataset.llm === "1";
  var MAXINPUT = parseInt(results.dataset.maxinput, 10) || 6000;
  var NAMES = { "0": "Brut", "1": "Nettoyage" };
  var EST = {};
  LEVELS.forEach(function (lv) { NAMES[String(lv.id)] = lv.name; EST[String(lv.id)] = lv.est || 0.4; });
  var queue = [];
  var cardSeq = 0;

  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }
  function human(n) {
    if (n < 1024) return n + " o";
    if (n < 1048576) return (n / 1024).toFixed(0) + " Ko";
    return (n / 1048576).toFixed(1) + " Mo";
  }
  function gainStr(raw, t) {
    if (!raw || t == null) return "—";
    var g = Math.round((raw - t) / raw * 100);
    return (g >= 0 ? "−" : "+") + Math.abs(g) + " %";
  }

  // ---- file d'attente -----------------------------------------------------
  function renderQueue() {
    queueEl.innerHTML = "";
    queue.forEach(function (item, i) {
      var li = el("li", "qitem");
      li.appendChild(el("span", "qname", item.file.name));
      li.appendChild(el("span", "qsize muted small", human(item.file.size)));
      var rm = el("button", "btn-ghost qrm", "×");
      rm.type = "button"; rm.title = "Retirer";
      rm.addEventListener("click", function () { queue.splice(i, 1); renderQueue(); });
      li.appendChild(rm);
      queueEl.appendChild(li);
    });
    go.disabled = queue.length === 0;
    clearQueueBtn.hidden = queue.length === 0;
    statusEl.textContent = queue.length ? queue.length + "/" + MAX_FILES + " fichier(s) en file" : "";
  }
  function addFiles(list) {
    var full = false;
    Array.prototype.slice.call(list).forEach(function (f) {
      var key = f.name + "|" + f.size + "|" + f.lastModified;
      if (queue.some(function (q) { return q.key === key; })) return;
      if (queue.length >= MAX_FILES) { full = true; return; }
      queue.push({ file: f, key: key });
    });
    input.value = "";
    renderQueue();
    if (full) statusEl.textContent = "Limite de " + MAX_FILES + " fichiers atteinte.";
  }

  drop.addEventListener("click", function () { input.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); }
  });
  input.addEventListener("change", function () { addFiles(input.files); });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("drag"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("drag"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });
  clearQueueBtn.addEventListener("click", function () { queue = []; renderQueue(); });

  // ---- résultats ----------------------------------------------------------
  function updateResultsBar() {
    var n = results.querySelectorAll(".result").length;
    resultsBar.hidden = n === 0;
    resultsCount.textContent = n + " résultat(s)";
  }
  clearResultsBtn.addEventListener("click", function () { results.innerHTML = ""; updateResultsBar(); });

  function download(md, name) {
    var blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    var a = el("a"); a.href = URL.createObjectURL(blob); a.download = name || "sortie.md"; a.click();
    URL.revokeObjectURL(a.href);
  }

  function buildCard(r) {
    var gid = "lvl-" + (cardSeq++);
    var tooLong = r.tokens_clean > MAXINPUT;
    var st = {
      mdName: r.md_name || "sortie.md",
      versions: { "0": r.markdown, "1": r.cleaned },
      tokens: { "0": r.tokens_raw, "1": r.tokens_clean },
      rows: {}, selected: null, busy: false,
    };
    var card = el("div", "result");
    var head = el("div", "result-head");
    head.appendChild(el("span", "result-name", r.name));
    head.appendChild(el("span", "result-engine badge", r.engine || "markitdown"));
    if (r.conv_fidelity) {
      var cf = r.conv_fidelity;
      if (cf.applicable) {
        var cmiss = cf.missing && cf.missing.length;
        var cb = el("span", "badge fid " + (cmiss ? "badge-warn" : "badge-ok"),
          "Conversion " + (cmiss ? "⚠ " : "✓ ") + cf.kept + "/" + cf.total + " chiffres");
        var ct = "Chiffres de la couche texte du PDF retrouvés dans la conversion : "
          + cf.kept + "/" + cf.total + " (PDF numérique uniquement).";
        if (cmiss) ct += " Manquants : " + cf.missing.join(", ") + ".";
        cb.title = ct;
        head.appendChild(cb);
      } else if (cf.reason === "scan/OCR" || cf.reason === "pas de couche texte") {
        var cb2 = el("span", "badge fid", "Conversion non vérifiée (scan)");
        cb2.title = "Le contrôle de fidélité de la conversion ne fonctionne que sur les PDF numériques (couche texte). Ici : scan/OCR.";
        head.appendChild(cb2);
      }
    }
    head.appendChild(el("span", "result-spacer"));
    var copyBtn = el("button", "btn-ghost act-copy", "Copier");
    var dlBtn = el("button", "act-dl", "Télécharger .md");
    var rmCard = el("button", "btn-ghost act-rm", "×");
    copyBtn.type = dlBtn.type = rmCard.type = "button"; rmCard.title = "Fermer";
    head.appendChild(copyBtn); head.appendChild(dlBtn); head.appendChild(rmCard);
    card.appendChild(head);

    var budgetRow = el("div", "budget-row");
    budgetRow.appendChild(el("span", "muted small", "Budget cible (tokens, optionnel) :"));
    var budgetInput = el("input", "budget-input");
    budgetInput.type = "number"; budgetInput.min = "0"; budgetInput.placeholder = "ex. 4000";
    budgetRow.appendChild(budgetInput);
    var reco = el("span", "muted small reco", "");
    budgetRow.appendChild(reco);
    card.appendChild(budgetRow);

    var table = el("table", "cmp");
    var thead = el("thead"), htr = el("tr");
    ["Niveau", "Tokens", "Gain", "Perte", ""].forEach(function (h) { htr.appendChild(el("th", null, h)); });
    thead.appendChild(htr); table.appendChild(thead);
    var tbody = el("tbody");

    function mkRow(id, label, loss, isLLM) {
      var tr = el("tr", "cmp-row");
      var cSel = el("td", "c-opt");
      var lab = el("label", "lvl-label");
      var radio = el("input", "lvl-radio"); radio.type = "radio"; radio.name = gid; radio.value = id;
      lab.appendChild(radio); lab.appendChild(el("span", null, label));
      cSel.appendChild(lab);
      var known = st.tokens[id] != null;
      var estTok = isLLM && !tooLong ? Math.round(st.tokens["1"] * EST[id]) : null;
      var tTok = el("td", "c-tok", known ? String(st.tokens[id]) : (estTok != null ? "~" + estTok : "—"));
      if (estTok != null && !known) tTok.title = "Estimation — cliquez « Calculer » pour la valeur réelle.";
      var tGain = el("td", "c-gain", known ? gainStr(st.tokens["0"], st.tokens[id])
        : (estTok != null ? "~" + gainStr(st.tokens["0"], estTok) : "—"));
      var tLoss = el("td", "c-loss", tooLong && isLLM ? "trop long → RAG" : loss);
      var tAct = el("td", "c-act");
      tr.appendChild(cSel); tr.appendChild(tTok); tr.appendChild(tGain); tr.appendChild(tLoss); tr.appendChild(tAct);
      tbody.appendChild(tr);
      st.rows[id] = { tr: tr, tok: tTok, gain: tGain, act: tAct, radio: radio, label: label, done: !isLLM, isLLM: isLLM };

      if (isLLM && tooLong) {
        radio.disabled = true; tr.classList.add("disabled");
      } else if (isLLM) {
        radio.disabled = true;  // sélectionnable seulement après « Calculer »
        var calc = el("button", "row-calc", "Calculer");
        calc.type = "button";
        calc.title = "Génère cette version via l'IA locale (~1 min). La ligne devient ensuite sélectionnable et téléchargeable.";
        calc.addEventListener("click", function () { evaluate(st, id); });
        tAct.appendChild(calc);
        st.rows[id].calc = calc;
      }
      radio.addEventListener("change", function () { if (radio.checked) onChoose(st, id); });
    }

    mkRow("0", "Brut", "aucune", false);
    mkRow("1", "Nettoyage", "aucune", false);
    if (LLM_ON) LEVELS.forEach(function (lv) { mkRow(String(lv.id), lv.name, lv.loss, true); });
    table.appendChild(tbody);
    card.appendChild(table);
    if (!LLM_ON) card.appendChild(el("p", "muted small", "Niveaux LLM indisponibles (moteur local éteint)."));
    var hint = el("p", "muted small hint",
      tooLong ? "Document trop long (> " + MAXINPUT + " tokens) : niveaux LLM désactivés — relève du RAG."
              : "Condensé/Synthèse : « ~ » est une estimation. Cliquez « Calculer » pour générer la version (quelques secondes) — elle devient alors sélectionnable et téléchargeable.");
    card.appendChild(hint);

    var pre = el("pre", "result-md"); var code = el("code"); pre.appendChild(code);
    card.appendChild(pre);

    st.els = { copyBtn: copyBtn, dlBtn: dlBtn, code: code, reco: reco, budgetInput: budgetInput };
    copyBtn.addEventListener("click", function () {
      navigator.clipboard.writeText(st.versions[st.selected] || "").then(function () {
        copyBtn.textContent = "Copié ✓";
        setTimeout(function () { copyBtn.textContent = "Copier"; }, 1500);
      });
    });
    dlBtn.addEventListener("click", function () { download(st.versions[st.selected] || "", dlName(st)); });
    rmCard.addEventListener("click", function () { card.remove(); updateResultsBar(); });
    budgetInput.addEventListener("input", function () {
      st.budget = parseInt(budgetInput.value, 10) || null; recommend(st);
    });

    setRadios(st, false);           // grise les radios LLM non calculés
    st.rows["1"].radio.checked = true;
    select(st, "1");
    return card;
  }

  function dlName(st) {
    var suffix = st.selected === "0" ? "" : "-" + NAMES[st.selected].toLowerCase();
    return st.mdName.replace(/\.md$/, "") + suffix + ".md";
  }
  function setRadios(st, disabled) {
    Object.keys(st.rows).forEach(function (k) {
      var row = st.rows[k];
      if (row.tr.classList.contains("disabled")) return;  // trop long : reste désactivé
      // un radio n'est sélectionnable que si son niveau est calculé (done)
      row.radio.disabled = disabled || !row.done;
    });
  }
  function onChoose(st, id) {
    if (st.busy) return;
    if (st.rows[id].done) select(st, id);
    else evaluate(st, id);
  }
  function select(st, id) {
    if (st.versions[id] == null) return;
    st.selected = id;
    st.rows[id].radio.checked = true;
    st.els.code.textContent = st.versions[id] || "(vide)";
    st.els.dlBtn.textContent = "Télécharger .md · " + NAMES[id];
    Object.keys(st.rows).forEach(function (k) { st.rows[k].tr.classList.toggle("sel", k === id); });
  }
  function recommend(st) {
    Object.keys(st.rows).forEach(function (k) { st.rows[k].tr.classList.remove("reco"); });
    if (!st.budget) { st.els.reco.textContent = ""; return; }
    var order = ["0", "1", "2", "3", "4"], choice = null;
    for (var i = 0; i < order.length; i++) {
      var id = order[i];
      if (st.tokens[id] != null && st.tokens[id] <= st.budget) { choice = id; break; }
    }
    if (!choice) for (var j = order.length - 1; j >= 0; j--) {
      if (st.tokens[order[j]] != null) { choice = order[j]; break; }
    }
    if (choice) { st.rows[choice].tr.classList.add("reco"); st.els.reco.textContent = "→ recommandé : " + NAMES[choice]; }
  }
  function evaluate(st, id) {
    if (st.busy) return;
    var prev = st.selected;
    st.busy = true; setRadios(st, true);
    if (st.rows[id].calc) { st.rows[id].calc.disabled = true; st.rows[id].calc.textContent = "…"; }
    st.rows[id].tok.textContent = "…";
    statusEl.textContent = "Optimisation « " + NAMES[id] + " » en cours…";
    fetch("/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ markdown: st.versions["1"], level: parseInt(id, 10) }),
    }).then(function (resp) {
      if (resp.status === 401) { window.location = "/login"; return null; }
      return resp.json().catch(function () { return { ok: false, error: "réponse serveur illisible (timeout proxy ?)" }; });
    }).then(function (d) {
      st.busy = false; setRadios(st, false);
      if (!d) return;
      if (!d.ok) {
        if (st.rows[id].calc) { st.rows[id].calc.disabled = false; st.rows[id].calc.textContent = "Réessayer"; }
        st.rows[id].tok.textContent = "⚠";
        statusEl.textContent = "Échec « " + NAMES[id] + " » : " + (d.error || "");
        if (prev) { st.rows[prev].radio.checked = true; }
        return;
      }
      st.versions[id] = d.markdown; st.tokens[id] = d.tokens; st.rows[id].done = true;
      if (st.rows[id].calc) { st.rows[id].calc.remove(); st.rows[id].calc = null; }
      if (d.fidelity && d.fidelity.total > 0) {
        var f = d.fidelity;
        var hasMiss = f.missing && f.missing.length;
        var hasAdd = f.added && f.added.length;
        var concern = hasAdd || (hasMiss && id === "2");  // Compactage doit tout conserver
        var cls = concern ? "badge badge-warn fid" : (!hasMiss && !hasAdd ? "badge badge-ok fid" : "badge fid");
        var b = el("span", cls, (hasAdd ? "⚠ " : (!hasMiss ? "✓ " : "")) + f.kept + "/" + f.total + " chiffres" + (d.repaired ? " ↻" : ""));
        var tt = "Valeurs numériques du source conservées : " + f.kept + "/" + f.total + ".";
        if (hasMiss) tt += " Manquantes : " + f.missing.join(", ") + ".";
        if (hasAdd) tt += " Inédites (à vérifier) : " + f.added.join(", ") + ".";
        if (d.repaired) tt += " Réintégration automatique des chiffres perdus (" + d.repaired + " passe(s)).";
        b.title = tt;
        st.rows[id].act.appendChild(b);
      }
      st.rows[id].tok.textContent = String(d.tokens);
      st.rows[id].gain.textContent = gainStr(st.tokens["0"], d.tokens);
      st.rows[id].tr.querySelector(".lvl-label span").textContent = NAMES[id] + (d.seconds ? " (" + d.seconds + "s)" : "");
      statusEl.textContent = "« " + NAMES[id] + " » : " + d.tokens + " tokens (" + gainStr(st.tokens["0"], d.tokens) + ")";
      select(st, id);
      recommend(st);
    }).catch(function () {
      st.busy = false; setRadios(st, false);
      if (st.rows[id].calc) { st.rows[id].calc.disabled = false; st.rows[id].calc.textContent = "Réessayer"; }
      st.rows[id].tok.textContent = "⚠";
      statusEl.textContent = "Erreur réseau ou délai dépassé (voir timeout NPM).";
      if (prev) { st.rows[prev].radio.checked = true; }
    });
  }

  // ---- conversion ---------------------------------------------------------
  go.addEventListener("click", function () {
    if (!queue.length) return;
    go.disabled = true;
    statusEl.textContent = "Conversion en cours…";
    var fd = new FormData();
    fd.append("csrf", csrf); fd.append("mode", modeSel.value);
    queue.forEach(function (q) { fd.append("files", q.file, q.file.name); });
    fetch("/convert", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } })
      .then(function (resp) {
        if (resp.status === 401) { window.location = "/login"; return null; }
        return resp.json();
      }).then(function (data) {
        if (!data) return;
        if (data.error) { statusEl.textContent = "Erreur : " + data.error; renderQueue(); return; }
        var ok = 0;
        (data.results || []).forEach(function (r) {
          if (r.ok) { ok++; results.appendChild(buildCard(r)); }
          else {
            var c = el("div", "result err");
            var h = el("div", "result-head");
            h.appendChild(el("span", "result-name", r.name));
            var rm = el("button", "btn-ghost act-rm", "×"); rm.type = "button";
            rm.addEventListener("click", function () { c.remove(); updateResultsBar(); });
            h.appendChild(el("span", "result-spacer")); h.appendChild(rm);
            c.appendChild(h);
            var pre = el("pre", "result-md"); pre.appendChild(el("code", null, "⚠ " + (r.error || "échec")));
            c.appendChild(pre); results.appendChild(c);
          }
        });
        updateResultsBar();
        queue = []; renderQueue();
        statusEl.textContent = ok + "/" + (data.results || []).length + " converti(s) — ajoutez d'autres fichiers si besoin";
      }).catch(function () { statusEl.textContent = "Erreur réseau."; renderQueue(); });
  });
})();
