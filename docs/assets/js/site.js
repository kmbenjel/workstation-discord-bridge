/* Warraq docs: theme, phone menu, copy buttons, on-page TOC and Arabic-aware search. No dependencies. */
(function () {
  "use strict";
  var doc = document.documentElement;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };
  var ar = doc.lang === "ar";

  /* Theme: follows the system until the reader picks one. */
  var toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) toggle.addEventListener("click", function () {
    var dark = doc.dataset.theme ? doc.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    doc.dataset.theme = dark ? "light" : "dark";
    store.set("warraq-theme", doc.dataset.theme);
  });

  /* The section menu starts folded on phones. */
  var side = document.querySelector("[data-side]");
  if (side && matchMedia("(max-width: 899px)").matches) side.removeAttribute("open");

  /* Copy buttons on code blocks. */
  document.querySelectorAll(".prose pre").forEach(function (pre) {
    var b = document.createElement("button");
    b.type = "button"; b.className = "copy"; b.textContent = ar ? "نسخ" : "Copy";
    b.addEventListener("click", function () {
      var text = pre.querySelector("code") ? pre.querySelector("code").innerText : pre.innerText;
      navigator.clipboard.writeText(text.replace(/\n$/, "")).then(function () {
        b.textContent = ar ? "نُسخ" : "Copied";
        setTimeout(function () { b.textContent = ar ? "نسخ" : "Copy"; }, 1400);
      });
    });
    pre.appendChild(b);
  });

  /* Highlight the current heading in the on-page TOC. */
  var tocLinks = document.querySelectorAll(".toc a");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var byId = {};
    tocLinks.forEach(function (a) { byId[decodeURIComponent(a.hash.slice(1))] = a; });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting && byId[e.target.id]) {
          tocLinks.forEach(function (a) { a.classList.remove("on"); });
          byId[e.target.id].classList.add("on");
        }
      });
    }, { rootMargin: "-70px 0px -70% 0px" });
    document.querySelectorAll(".prose h2[id], .prose h3[id]").forEach(function (h) { io.observe(h); });
  }

  /* ── Search ─────────────────────────────────────────────
     fold() mirrors src/warraq_site/textnorm.py (and arabic-bib's search fold): tashkeel and tatweel out,
     alef forms → ا, ة→ه, ى→ي, ؤ→و, ئ→ي, ء out, presentation forms by NFKC, ی→ي, ک→ك, Eastern digits → ASCII,
     punctuation → space, Latin lower-cased. foldMap() keeps, for each folded character, its index in the source,
     so that matches can be highlighted in the original text. */
  /* fold:start */
  var MAP = { "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ء": "", "ی": "ي", "ک": "ك" };
  var TASHKEEL = /[ؐ-ًؚ-ٰٟۖ-ۭـ]/;
  var WORD = /[\p{L}\p{N}]/u;
  var PREFIXES = ["وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك"];

  function foldChar(c) {
    var code = c.charCodeAt(0);
    if ((code >= 0xfb50 && code <= 0xfdff) || (code >= 0xfe70 && code <= 0xfeff)) {
      var n = c.normalize("NFKC");
      if (n !== c) { var o = ""; for (var k = 0; k < n.length; k++) o += foldChar(n[k]); return o; }
    }
    if (TASHKEEL.test(c)) return "";
    if (c in MAP) return MAP[c];
    if (code >= 0x660 && code <= 0x669) return String(code - 0x660);
    if (code >= 0x6f0 && code <= 0x6f9) return String(code - 0x6f0);
    if (c === "_" || !WORD.test(c)) return " ";
    return c.toLowerCase();
  }
  function foldMap(s) {
    var out = "", map = [];
    for (var i = 0; i < s.length; i++) {
      var f = foldChar(s[i]);
      for (var j = 0; j < f.length; j++) { out += f[j]; map.push(i); }
    }
    return { s: out, map: map };
  }
  function fold(s) { return foldMap(s).s.split(/\s+/).filter(Boolean).join(" "); }
  function stem(w) {
    for (var i = 0; i < PREFIXES.length; i++) {
      var p = PREFIXES[i];
      if (w.indexOf(p) === 0 && w.length - p.length >= 3) return w.slice(p.length);
    }
    return w;
  }
  /* fold:end */

  var dlg = document.getElementById("search");
  var input = document.getElementById("q");
  var list = document.getElementById("results");
  var empty = document.getElementById("no-results");
  var script = document.querySelector("script[data-index]");
  var index = null, loading = null, sel = -1;

  function load() {
    if (index || loading) return loading;
    loading = fetch(script.dataset.index).then(function (r) { return r.json(); }).then(function (docs) {
      index = docs.map(function (d) {
        return { d: d, t: fold(d.t), h: fold(d.h), x: foldMap(d.x) };
      });
    }).catch(function () { index = []; });
    return loading;
  }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function snippet(entry, terms) {
    var fx = entry.x, at = -1, len = 0;
    terms.forEach(function (t) { var i = fx.s.indexOf(t); if (i >= 0 && (at < 0 || i < at)) { at = i; len = t.length; } });
    var text = entry.d.x;
    if (at < 0) return esc(text.slice(0, 140));
    var start = fx.map[at], end = fx.map[Math.min(at + len, fx.map.length) - 1] + 1;
    var from = Math.max(0, start - 50), to = Math.min(text.length, end + 90);
    while (from > 0 && WORD.test(text[from - 1])) from--;
    return (from > 0 ? "… " : "") + esc(text.slice(from, start)) + "<mark>" + esc(text.slice(start, end)) + "</mark>" + esc(text.slice(end, to)) + (to < text.length ? " …" : "");
  }

  function run() {
    var q = fold(input.value);
    list.innerHTML = ""; sel = -1;
    if (!q) { empty.hidden = true; return; }
    var terms = q.split(" ").map(stem);
    var hits = [];
    index.forEach(function (e) {
      var score = 0;
      for (var i = 0; i < terms.length; i++) {
        var t = terms[i], s = 0;
        if (e.t.indexOf(t) >= 0) s += 10;
        if (e.h.indexOf(t) >= 0) s += 6;
        var n = 0, k = e.x.s.indexOf(t);
        while (k >= 0 && n < 5) { n++; k = e.x.s.indexOf(t, k + t.length); }
        s += n;
        if (!s) return;
        score += s;
      }
      if (!e.d.h) score += 2;
      hits.push({ e: e, score: score });
    });
    hits.sort(function (a, b) { return b.score - a.score; });
    hits.slice(0, 20).forEach(function (h) {
      var d = h.e.d, li = document.createElement("li");
      var crumb = d.s && d.s !== d.t ? '<span class="r-s" dir="auto">' + esc(d.s) + "</span>" : "";
      li.innerHTML = '<a href="' + esc(d.u) + '">' + crumb + '<span class="r-t">' + esc(d.h ? d.t + " › " + d.h : d.t) +
        '</span><span class="r-x">' + snippet(h.e, terms) + "</span></a>";
      list.appendChild(li);
    });
    empty.hidden = hits.length > 0;
  }
  function move(step) {
    var links = list.querySelectorAll("a");
    if (!links.length) return;
    if (sel >= 0) links[sel].removeAttribute("aria-selected");
    sel = (sel + step + links.length) % links.length;
    links[sel].setAttribute("aria-selected", "true");
    links[sel].scrollIntoView({ block: "nearest" });
  }
  function open() {
    if (!dlg || dlg.open) return;
    dlg.showModal(); input.focus(); input.select();
    load().then(run);
  }
  document.querySelectorAll("[data-search-open]").forEach(function (b) { b.addEventListener("click", open); });
  if (dlg) {
    input.addEventListener("input", function () { load().then(run); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") {
        var links = list.querySelectorAll("a");
        if (links.length) { e.preventDefault(); location.href = links[Math.max(sel, 0)].href; dlg.close(); }
      }
    });
    dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });
    list.addEventListener("click", function (e) { if (e.target.closest("a")) dlg.close(); });
  }
  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement || {}).tagName;
    if ((e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) && tag !== "INPUT" && tag !== "TEXTAREA") {
      e.preventDefault(); open();
    }
  });
  var params = new URLSearchParams(location.search);
  if (params.get("q") && input) { input.value = params.get("q"); open(); }
})();
