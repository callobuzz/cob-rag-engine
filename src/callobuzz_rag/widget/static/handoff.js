/*! cob-rag-engine widget: handoff card helpers. (c) 2026 Callobuzz | SPDX-License-Identifier: MIT. Pure functions, no DOM at load time. ES5. */
(function (root) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Only https:, tel: and mailto: ever become a link, whatever the server sent.
  function isSafeHref(url) {
    var u = String(url == null ? "" : url).replace(/[\u0000-\u001f\u007f\s]+/g, "");
    var m = /^([a-z][a-z0-9+.-]*):/i.exec(u);
    if (!m) return false;
    var scheme = m[1].toLowerCase();
    return scheme === "https" || scheme === "tel" || scheme === "mailto";
  }

  function inline(text) {
    var md = root.CobMd;
    return md && typeof md.inline === "function" ? md.inline(text) : esc(text);
  }

  var LABELS = {
    en: { open: "Talk to our team", closed: "Leave us a message" },
    hi: { open: "हमारी टीम से बात करें", closed: "हमें संदेश छोड़ें" },
    "hi-Latn": { open: "Hamari team se baat karein", closed: "Hamein message chhodiye" }
  };

  // The card: the message (owner text, Markdown) and one link per channel; a callback has no
  // link, it opens the lead form, so it is a button. Everything is escaped here.
  function cardHtml(block, lang) {
    var words = LABELS[lang] || LABELS.en;
    var out = ['<div class="card" role="group" aria-label="' + esc(block.open_now ? words.open : words.closed) + '">'];
    out.push('<div class="card-msg">' + inline(block.message || "") + "</div>");
    out.push('<div class="chans">');
    var channels = block.channels || [];
    for (var i = 0; i < channels.length; i++) {
      var c = channels[i] || {};
      if (c.kind === "callback") {
        out.push('<button class="chan" type="button" data-kind="callback">' + esc(c.label) + "</button>");
      } else if (isSafeHref(c.url)) {
        out.push(
          '<a class="chan" href="' + esc(c.url) + '" target="_blank" rel="noopener noreferrer" data-kind="' +
            esc(c.kind) + '">' + esc(c.label) + "</a>"
        );
      }
    }
    out.push("</div></div>");
    return out.join("");
  }

  // The visitor's pick on the callback form: a day and one of the offered "HH:MM-HH:MM" windows.
  // null when either is missing or the window is not a forward range; the server checks it again.
  function callbackWindow(dateValue, windowValue) {
    var d = String(dateValue || "");
    var w = /^(\d{2}:\d{2})-(\d{2}:\d{2})$/.exec(String(windowValue || ""));
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d) || !w) return null;
    if (w[2] <= w[1]) return null;
    return { date: d, start: w[1], end: w[2] };
  }

  root.CobHandoff = { cardHtml: cardHtml, isSafeHref: isSafeHref, callbackWindow: callbackWindow };
})(typeof window !== "undefined" ? window : globalThis);
