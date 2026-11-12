/*! cob-rag-engine widget freshness sensor. (c) 2026 Callobuzz | SPDX-License-Identifier: MIT */
/* The widget's freshness sensor (window.CobSensor), joined in front of widget.js.
 *
 * When the business has learned its website, the widget lets the engine know that a page may have
 * changed: on a sample of page views it sends the page's address and a short code (a 64-bit hash)
 * of the page's visible main text. Never the text, never anything a visitor typed. The engine only
 * compares these codes with each other; when one changes it reads the page itself, from the
 * server, and the server's reading decides what is learned.
 *
 * Quiet by design: once per page per browser per 24 hours, only on a sampled share of views, only
 * after the page has settled, never on a page with a query string or a password field, never on
 * the paths the engine says to skip (login, cart, search, anything the owner left out), and every
 * failure (storage blocked, no BigInt, network) just means no report.
 */
(function () {
  "use strict";

  var DAY_MS = 24 * 60 * 60 * 1000;
  var MAX_TEXT = 50000;
  var DELAY_MS = 3000;
  var reportedThisLoad = false;

  // FNV-1a, 64 bit, over the UTF-8 bytes, as 16 hex characters. Needs BigInt (no literals, so an
  // old browser can still parse this file); without it there is simply no report.
  function fnv1a64(text) {
    if (typeof BigInt !== "function") return null;
    var bytes;
    if (typeof TextEncoder === "function") bytes = new TextEncoder().encode(text);
    else {
      var bin = unescape(encodeURIComponent(text));
      bytes = [];
      for (var i = 0; i < bin.length; i++) bytes.push(bin.charCodeAt(i));
    }
    var h = BigInt("0xcbf29ce484222325");
    var prime = BigInt("0x100000001b3");
    for (var j = 0; j < bytes.length; j++) {
      h = BigInt.asUintN(64, (h ^ BigInt(bytes[j])) * prime);
    }
    var hex = h.toString(16);
    while (hex.length < 16) hex = "0" + hex;
    return hex;
  }

  function collapse(text) {
    return String(text || "").replace(/\s+/g, " ").trim().toLowerCase().slice(0, MAX_TEXT);
  }

  // The visible main text: <main>, else <article>, else <body>.
  function mainText(doc) {
    var el = doc.querySelector("main") || doc.querySelector("article") || doc.body;
    if (!el) return "";
    return collapse(el.innerText != null ? el.innerText : el.textContent);
  }

  // The rule as a whole path segment, at the start of the path or after any "/": "/feed" skips
  // "/feed", "/feed/x" and "/blog/feed", never "/feedback" (the server matches the same way).
  function atSegment(p, rule) {
    var from = 0;
    while (from <= p.length) {
      var rest = p.slice(from);
      if (rest === rule || rest.indexOf(rule + "/") === 0) return true;
      var next = p.indexOf("/", from + 1);
      if (next < 0) return false;
      from = next;
    }
    return false;
  }

  function skipped(path, prefixes) {
    var p = String(path || "/").toLowerCase();
    for (var i = 0; i < (prefixes || []).length; i++) {
      var rule = String(prefixes[i] || "").toLowerCase();
      if (!rule) continue;
      if (rule.indexOf("*") >= 0) {
        var source = rule.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
        if (new RegExp("^" + source).test(p)) return true;
      } else if (atSegment(p, rule)) {
        return true;
      }
    }
    return false;
  }

  function defer(fn) {
    setTimeout(function () {
      if (typeof requestIdleCallback === "function") requestIdleCallback(fn);
      else fn();
    }, DELAY_MS);
  }

  function seenRecently(store, key, now) {
    try {
      var at = Number(store.getItem(key));
      return at > 0 && now - at < DAY_MS;
    } catch (e) {
      return false;
    }
  }

  function remember(store, key, now) {
    try {
      store.setItem(key, String(now));
    } catch (e) {
      /* blocked storage: the once-per-load flag still holds */
    }
  }

  // run(api, key, cfg[, env]): `cfg` is the `sensor` block of /widget/config ({sample, skip}).
  // `env` lets a test hand in a fake page; the widget passes nothing.
  function run(api, key, cfg, env) {
    try {
      if (!cfg || !(cfg.sample > 0) || reportedThisLoad) return;
      var e = env || {};
      var doc = e.document || document;
      var loc = e.location || location;
      var store = e.localStorage;
      if (store === undefined) {
        try {
          store = localStorage;
        } catch (err) {
          store = null;
        }
      }
      var random = e.random || Math.random;
      var post = e.fetch || (typeof fetch === "function" ? fetch.bind(window) : null);
      var now = (e.now || Date.now)();
      if (!post || random() >= cfg.sample) return;
      if (loc.search || String(loc.href || "").indexOf("?") >= 0) return;
      if (doc.querySelector('input[type="password"]')) return;
      if (skipped(loc.pathname, cfg.skip)) return;
      var url = loc.origin + loc.pathname;
      var memo = "cobrag:sensor:" + fnv1a64(url);
      if (store && seenRecently(store, memo, now)) return;
      (e.defer || defer)(function () {
        try {
          if (reportedThisLoad) return;
          var fingerprint = fnv1a64(mainText(doc));
          if (!fingerprint) return;
          reportedThisLoad = true;
          if (store) remember(store, memo, now);
          var result = post(api + "/api/v1/widget/page", {
            method: "POST",
            keepalive: true,
            headers: { "X-Widget-Key": key, "Content-Type": "application/json" },
            body: JSON.stringify({ page_url: url, fingerprint: fingerprint }),
          });
          if (result && typeof result.catch === "function") result.catch(function () {});
        } catch (err) {
          /* a sensor must never disturb the page */
        }
      });
    } catch (err) {
      /* ditto */
    }
  }

  window.CobSensor = { run: run, fnv1a64: fnv1a64, mainText: mainText, skipped: skipped };
  window.CobSensor._reset = function () {
    reportedThisLoad = false;
  };
})();
