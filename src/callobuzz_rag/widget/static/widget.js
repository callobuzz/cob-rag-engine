/*!
 * cob-rag-engine chat widget. (c) 2026 Callobuzz | SPDX-License-Identifier: MIT.
 *
 *   <script src="https://YOUR-ENGINE/widget.js" data-key="pk_..." async></script>
 *
 * MIT License
 *
 * Copyright (c) 2026 Callobuzz
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 *
 * Optional attributes:
 *   data-api       engine URL (default: where this script was loaded from)
 *   data-open      "true" to start opened
 *   data-position  "left" | "right"   (overrides the dashboard setting)
 *   data-color     "#4f46e5"          (overrides the dashboard setting)
 *   data-ask       text put in the message box when it opens (never sent for the visitor; max 200)
 *
 * Everything renders inside a Shadow DOM, so the host page's CSS cannot break
 * the widget and the widget's CSS cannot leak into the page. Model output is
 * never inserted as HTML: markdown.js (served in front of this file) builds
 * DOM nodes for a safe Markdown subset, with citation chips for the sources.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || window.__cobRagWidgetLoaded) return;
  window.__cobRagWidgetLoaded = true;

  var KEY = script.getAttribute("data-key");
  if (!KEY) {
    console.error("[cob-rag widget] missing data-key attribute");
    return;
  }
  var API = (script.getAttribute("data-api") || new URL(script.src).origin).replace(/\/+$/, "");
  var STORE = "cobrag:" + KEY.slice(0, 16);
  // The assistant's opaque chat-session token (from its `meta` event): the engine keeps the
  // conversation, this tab only remembers which one is ours.
  var SESSION = STORE + ":session";
  var MAX_HISTORY = 20;
  // Refusals shown as an ordinary assistant reply (the engine's `detail` is already
  // written for a website visitor): workspace not active, monthly question limit.
  var POLITE_CODES = ["workspace_not_active", "question_cap_reached", "handoff_rate_limited", "demo_limit", "demo_unavailable"];
  // The engine's `stage` events, in words a visitor understands (`stage_<name>` in widget-core.js).
  // Shown only after a wait.
  var STAGE_AFTER_MS = 2000;
  // L1 release
  // Always shown in the panel footer as plain text (never a link); no option or config field can hide it.
  // Must equal POWERED_BY in accounts/settings_store.py (tests/unit/test_widget_powered_by.py).
  var POWERED_TEXT = "cob-rag-engine by Callobuzz";
  // /L1 release

  var cfg = {
    title: "Ask us anything",
    greeting: "Hi! How can I help you today?",
    placeholder: "Type your question…",
    primary_color: "#4f46e5",
    position: "right",
    show_sources: true,
    assistant_name: null,
    ai_disclosure: null,
    language: null,
    starters: [],
    proactive: [],
    sensor: null, // BIZ2 sensor
    human_button: false, // TV1 human
    privacy_url: null, // P3 legal
    ai_badge: null, // TV5 disclosure
    disclosure_line: null, // TV5 disclosure
    disclosure_lines: {}, // TV5 disclosure
    // The footer line follows the config: a config without the key keeps the line; null hides it.
    powered_by: { text: POWERED_TEXT },
  };

  function footHtml() {
    var parts = [];
    if (cfg.powered_by && cfg.powered_by.text) parts.push(cfg.powered_by.text);
    // P3 legal: a "Privacy" link next to the line, only when the engine sent an address
    // (the address is set on the element after the panel is drawn, see wirePrivacy: no link is built
    // as a string here, so the "Powered by" line can never become one)
    var link = /^https?:\/\//i.test(String(cfg.privacy_url || ""))
      ? '<a class="privacy" target="_blank" rel="noopener">' + esc(t("privacy")) + "</a>"
      : "";
    if (!parts.length && !link) return "";
    var text = parts.length ? '<span class="powered">' + esc(parts.join(" · ")) + "</span>" : "";
    return '<div class="foot">' + text + (text && link ? " · " : "") + link + "</div>";
    // /P3 legal
  }
  // P3 legal
  function wirePrivacy(root) {
    var url = String(cfg.privacy_url || "");
    var a = root.querySelector(".foot a.privacy");
    if (a && /^https?:\/\//i.test(url)) a.setAttribute("href", url);
  }
  // /P3 legal

  // ---------------------------------------------------------------- helpers
  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // Answers are Markdown. markdown.js (served in front of this script as one file) turns it into
  // DOM nodes: model text is escaped or set as text, never parsed as HTML. [n] markers become
  // source chips when the engine sent that source, and stay plain text otherwise.
  var MD = window.CobMd;
  // markdown.js always travels in front of this file; if it is missing the answer shows as plain text.
  if (!MD) MD = { stream: function (host) { return { update: function (t) { host.textContent = t; } }; } };

  // widget-core.js (language, page globs, greeting frequency) is served in front of this file; if it is
  // missing the widget still works, it just shows no greeting.
  var EN = {
    send: "Send", yourMessage: "Your message", newChat: "Start a new conversation",
    newChatTitle: "New conversation", closeChat: "Close chat", openChat: "Open chat", close: "Close",
    document: "Document", noAnswer: "Sorry, I couldn't find an answer to that.",
    cantAnswer: "The assistant could not answer.", network: "Network error", bookNow: "Book now"
  };
  var CORE = window.CobWidgetCore || {
    tr: function (k) { return EN[k] || k; }, setLang: function () {}, getLang: function () { return "en"; },
    pickLanguage: function () { return "en"; }, proactiveDue: function () { return false; },
    markShown: function () {}, dismissForever: function () {}
  };
  function t(key, vars) { return CORE.tr(key, vars); }
  // Spaces and tabs before a marker go with it, newlines stay (they are block structure).
  var CITE_RE = /[ \t]*(?:\[\^?\d+(?:\s*[,–-]\s*\d+)*\]|【\s*\d+\s*】)/g;
  var CARET_RE = /([A-Za-zÀ-ɏ.,;:!?)"'])[ \t]*\^\d{1,2}(?![A-Za-z0-9])/g;

  // What the answer cites: the engine's numbers, or none when the owner hides sources.
  function mdOpts(sources) {
    var nums = [];
    (sources || []).forEach(function (s) { if (s.index != null) nums.push(Number(s.index)); });
    return { sources: nums };
  }

  // Replaces what is in `host` with the rendered Markdown (once; for finished text).
  function mdInto(host, text, sources) {
    while (host.firstChild) host.removeChild(host.firstChild);
    var box = document.createElement("div");
    box.className = "md";
    host.appendChild(box);
    host._sources = sources || [];
    MD.stream(box, mdOpts(host._sources)).update(visibleText(text), true);
  }

  function visibleText(text) {
    text = String(text);
    if (cfg.show_sources) return text;
    // Sources hidden by the owner: the markers would point at nothing.
    return text.replace(CITE_RE, "").replace(CARET_RE, "$1").replace(/^[\s,;:]+/, "").replace(/[ \t]+([,.;:!?])/g, "$1").trim();
  }

  function load() {
    try { return JSON.parse(sessionStorage.getItem(STORE) || "[]"); } catch (e) { return []; }
  }
  function save(history) {
    try { sessionStorage.setItem(STORE, JSON.stringify(history.slice(-MAX_HISTORY))); } catch (e) {}
  }

  // ------------------------------------------------------------------ DOM
  var root = null; // the widget's Shadow DOM, created once the page body exists

  function css(color, pos) {
    return [
      ":host{all:initial}",
      "*{box-sizing:border-box;font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Noto Sans','Noto Sans Devanagari','Nirmala UI','Mangal',sans-serif}",
      ".wrap{position:fixed;bottom:20px;" + pos + ":20px;z-index:2147483000;display:flex;flex-direction:column;align-items:" + (pos === "left" ? "flex-start" : "flex-end") + ";gap:12px}",
      ".launcher{width:58px;height:58px;border-radius:50%;border:0;cursor:pointer;background:" + color + ";color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.2);display:grid;place-items:center;transition:transform .15s}",
      ".launcher:hover{transform:scale(1.06)}.launcher:focus-visible{outline:3px solid #fff;outline-offset:2px}",
      ".launcher svg{width:26px;height:26px}",
      ".panel{width:380px;max-width:calc(100vw - 40px);height:560px;max-height:calc(100vh - 110px);background:#fff;color:#111827;border-radius:16px;box-shadow:0 16px 48px rgba(0,0,0,.22);display:none;flex-direction:column;overflow:hidden}",
      ".panel.open{display:flex;animation:pop .18s ease-out}",
      "@keyframes pop{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}",
      ".head{background:" + color + ";color:#fff;padding:14px 16px;display:flex;align-items:center;justify-content:space-between;gap:8px}",
      ".head h2{margin:0;font-size:15px;font-weight:600;line-height:1.3}",
      ".head .sub{margin:2px 0 0;font-size:11.5px;opacity:.85;font-weight:400}",
      // TV5 disclosure: the AI badge
      ".ai-badge{display:inline-block;border:1px solid rgba(255,255,255,.7);border-radius:999px;padding:1px 8px;font-size:11px;font-weight:500;line-height:1.4}",
      ".head .actions{display:flex;align-items:center;gap:2px}",
      // TV1 human: the "Talk to a person" button
      ".human{background:transparent;border:1px solid rgba(255,255,255,.65);color:#fff;cursor:pointer;font-size:12px;line-height:1.2;padding:4px 10px;margin-right:4px;border-radius:999px;white-space:nowrap}",
      ".human:hover{background:rgba(255,255,255,.15)}.human:focus-visible{outline:2px solid #fff;outline-offset:2px}",
      ".icon-btn{background:transparent;border:0;color:#fff;cursor:pointer;padding:4px;border-radius:6px;display:grid;place-items:center;opacity:.9}",
      ".icon-btn:hover{opacity:1;background:rgba(255,255,255,.15)}",
      ".msgs{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:#f9fafb}",
      ".msg{max-width:85%;padding:10px 13px;border-radius:14px;font-size:14px;line-height:1.5;word-wrap:break-word}",
      ".msg[lang=hi]{line-height:1.65}",
      ".msg p{margin:0}.msg ul,.msg ol{margin:0;padding-left:20px}.msg li{margin:2px 0 0}.msg li>ul,.msg li>ol{margin-top:2px}",
      ".msg code{background:rgba(0,0,0,.06);padding:1px 4px;border-radius:4px;font-family:ui-monospace,monospace;font-size:12.5px}",
      ".msg a{color:inherit;text-decoration:underline;word-break:break-word}",
      // Markdown blocks (markdown.js): spacing between blocks, headings, code, quotes, tables, citation chips.
      ".md-stable>*,.md-tail>*{margin-top:8px}.md-stable>:first-child,.md-stable:empty+.md-tail>:first-child{margin-top:0}",
      ".msg h3,.msg h4,.msg h5,.msg h6{margin:0;font-size:14px;font-weight:700;line-height:1.35}.msg h3{font-size:15.5px}.msg h4{font-size:14.5px}",
      ".msg pre{margin:0;background:rgba(0,0,0,.05);border:1px solid rgba(0,0,0,.08);border-radius:8px;padding:8px 10px;overflow-x:auto;font-size:12.5px;line-height:1.45}.msg pre code{background:none;padding:0;white-space:pre}",
      ".msg blockquote{margin:0;padding:0 0 0 10px;border-left:3px solid rgba(0,0,0,.18);opacity:.85}.msg hr{border:0;border-top:1px solid rgba(0,0,0,.12);margin:10px 0 2px}",
      ".md-table{overflow-x:auto;max-width:100%;border:1px solid #e5e7eb;border-radius:8px;-webkit-overflow-scrolling:touch}.md-table:focus-visible{outline:2px solid " + color + ";outline-offset:1px}",
      ".md-table table{border-collapse:collapse;font-size:13px;width:max-content;min-width:100%}.md-table th,.md-table td{padding:5px 9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}",
      ".md-table th{background:#f3f4f6;font-weight:600;white-space:nowrap}.md-table tr:last-child td{border-bottom:0}.md-table .md-center{text-align:center}.md-table .md-right{text-align:right}",
      ".md-cite{font:600 10.5px/1 ui-monospace,monospace;min-width:17px;height:17px;padding:0 4px;margin:0 1px;border-radius:999px;border:1px solid " + color + "55;background:" + color + "14;color:" + color + ";cursor:pointer;vertical-align:1px}",
      ".md-cite:hover{background:" + color + "26}.md-cite:focus-visible{outline:2px solid " + color + ";outline-offset:1px}",
      ".cite-pop{margin-top:8px;border:1px solid #e5e7eb;border-radius:10px;background:#f9fafb;padding:8px 10px;font-size:12.5px;color:#374151}.cite-pop b{display:block;font-size:12px;color:#111827;margin-bottom:2px}.cite-pop p{margin:0;white-space:pre-wrap;word-break:break-word}",
      ".bot{align-self:flex-start;background:#fff;border:1px solid #e5e7eb;border-bottom-left-radius:4px}",
      ".user{align-self:flex-end;background:" + color + ";color:#fff;border-bottom-right-radius:4px}",
      ".err{align-self:center;background:#fef2f2;color:#991b1b;border:1px solid #fecaca;font-size:13px}",
      ".sources{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}",
      ".src{font-size:11.5px;background:#f3f4f6;border:1px solid #e5e7eb;border-radius:999px;padding:2px 9px;color:#374151;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".typing{display:inline-flex;gap:4px;padding:4px 0}.typing i{width:6px;height:6px;border-radius:50%;background:#9ca3af;animation:b 1s infinite}",
      ".typing i:nth-child(2){animation-delay:.15s}.typing i:nth-child(3){animation-delay:.3s}",
      ".wait{display:inline-flex;align-items:center;gap:8px}.stage{font-size:12.5px;color:#6b7280}.stage:empty{display:none}",
      "@keyframes b{0%,60%,100%{opacity:.3;transform:none}30%{opacity:1;transform:translateY(-3px)}}",
      "form{display:flex;gap:8px;padding:12px;border-top:1px solid #e5e7eb;background:#fff}",
      "textarea{flex:1;resize:none;border:1px solid #d1d5db;border-radius:10px;padding:9px 11px;font-size:14px;line-height:1.4;max-height:120px;outline:none;color:#111827;background:#fff}",
      "textarea:focus{border-color:" + color + ";box-shadow:0 0 0 3px " + color + "22}",
      ".send{border:0;border-radius:10px;background:" + color + ";color:#fff;padding:0 14px;cursor:pointer;font-size:14px;font-weight:600}",
      ".send:disabled{opacity:.5;cursor:not-allowed}",
      ".foot{flex:none;text-align:center;font-size:11px;line-height:1.4;padding:0 0 8px;background:#fff}",
      ".powered{color:#4b5563}",
      ".foot a.privacy{color:#4b5563;text-decoration:underline}", // P3 legal
      // The proactive greeting: a small bubble above the launcher (never over an open panel).
      ".nudge{max-width:260px;background:#fff;color:#111827;border:1px solid #e5e7eb;border-radius:14px;box-shadow:0 8px 24px rgba(0,0,0,.15);padding:10px 30px 10px 12px;font-size:14px;line-height:1.45;position:relative;cursor:pointer;animation:pop .18s ease-out}",
      ".nudge .md p{margin:0}.nudge .md a{color:inherit}",
      ".nudge .x{position:absolute;top:4px;right:4px;border:0;background:transparent;cursor:pointer;color:#6b7280;padding:2px;border-radius:6px;display:grid;place-items:center}.nudge .x:hover{color:#111827}",
      ".nudge .off{display:block;margin-top:6px;border:0;background:transparent;color:#4b5563;font-size:12px;text-decoration:underline;cursor:pointer;padding:0}",
      ".nudge .go{display:block;margin-top:6px;border:0;background:transparent;color:" + color + ";font-size:12.5px;font-weight:600;cursor:pointer;padding:0}",
      ".nudge .go:focus-visible,.nudge .x:focus-visible,.nudge .off:focus-visible{outline:2px solid " + color + ";outline-offset:1px}",
      "@media (prefers-reduced-motion:reduce){.panel.open,.nudge{animation:none}}",
      "@media (max-width:480px){.wrap{bottom:12px;" + pos + ":12px}.panel.open{position:fixed;inset:0;width:100vw;max-width:none;height:100%;max-height:none;border-radius:0}}",
      // Suggestion chips and the one call-to-action button (conversation C6).
      ".chips{display:flex;flex-wrap:wrap;gap:6px;align-self:flex-start;max-width:100%}",
      ".chip{border:1px solid " + color + "55;background:#fff;color:" + color + ";border-radius:999px;padding:6px 12px;font-size:13px;line-height:1.3;cursor:pointer;text-align:left;max-width:100%}",
      ".chip:hover{background:" + color + "14}.chip:focus-visible{outline:2px solid " + color + ";outline-offset:1px}",
      ".cta{align-self:flex-start;display:flex;flex-wrap:wrap;align-items:center;gap:4px;max-width:100%}",
      ".cta-btn{display:inline-block;border:0;border-radius:10px;background:" + color + ";color:#fff;padding:9px 14px;font-size:13.5px;font-weight:600;line-height:1.2;cursor:pointer;text-decoration:none}",
      ".cta-btn:hover{filter:brightness(1.08)}.cta-btn:focus-visible{outline:3px solid " + color + "55;outline-offset:2px}",
      ".cta-x{border:0;background:transparent;color:#4b5563;cursor:pointer;font-size:12.5px;padding:6px 8px;border-radius:8px}.cta-x:hover{color:#111827;background:rgba(0,0,0,.06)}.cta-x:focus-visible{outline:2px solid " + color + ";outline-offset:1px}",
      // The lead form (lead_form.js helpers, built below): a card in the chat, never a popup.
      ".lead{align-self:stretch;background:#fff;color:#111827;border:1px solid #e5e7eb;border-radius:14px;padding:12px;font-size:13.5px}",
      ".lead h3{margin:0 0 6px;font-size:14px}",
      ".lead .notice{font-size:12px;line-height:1.45;color:#4b5563;margin:0 0 8px}.lead .notice a{color:inherit;text-decoration:underline}",
      ".lead form{display:block;padding:0;border:0;gap:0;background:none}",
      ".lead label{display:block;margin:8px 0 2px;font-weight:600}",
      ".lead input,.lead select{width:100%;border:1px solid #d1d5db;border-radius:8px;padding:7px 9px;font-size:14px;color:#111827;background:#fff}",
      ".lead input:focus,.lead select:focus{outline:2px solid " + color + ";outline-offset:1px}",
      ".lead input[aria-invalid=true],.lead select[aria-invalid=true]{border-color:#b91c1c}",
      ".lead .ferr{display:block;color:#b91c1c;font-size:12px;margin-top:2px}",
      ".lead .consent{display:flex;gap:6px;align-items:flex-start;margin-top:10px;font-weight:400}.lead .consent input{width:auto;margin-top:2px}",
      ".lead .hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}",
      ".lead button[type=submit]{margin-top:10px;width:100%;border:0;border-radius:8px;padding:9px;background:" + color + ";color:#fff;font-weight:600;font-size:14px;cursor:pointer}",
      ".lead button[type=submit]:disabled{opacity:.5;cursor:not-allowed}",
      ".lead .lstatus{margin:8px 0 0;font-size:12.5px;color:#4b5563}.lead .lstatus:empty{display:none}",
      ".lead .book{display:inline-block;margin:2px 0 8px;padding:8px 14px;border-radius:8px;background:" + color + ";color:#fff;font-size:13.5px;font-weight:600;text-decoration:none}.lead .book:focus-visible{outline:3px solid " + color + "55;outline-offset:2px}",
      ".lead .thanks{margin:0 0 6px;font-size:14px}.lead .withdraw{font-size:12px;color:#4b5563}",
      // The handoff card (handoff.js): inside a bot bubble, so the link rules name `.msg` to win over `.msg a`.
      ".card{margin-top:8px;padding:10px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb}",
      ".card-msg{font-size:13.5px;margin-bottom:8px}",
      ".chans{display:flex;flex-wrap:wrap;gap:6px}",
      ".msg .chan{display:inline-block;font:inherit;font-size:13px;line-height:1.3;padding:6px 11px;border-radius:999px;border:1px solid " + color + ";color:" + color + ";background:#fff;text-decoration:none;cursor:pointer}",
      ".msg .chan:hover{background:" + color + ";color:#fff}.msg .chan:focus-visible{outline:2px solid " + color + ";outline-offset:2px}"

    ].join("");
  }

  var CHAT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  var CLOSE_ICON = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
  var RESET_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>';

  var el = {};
  var history = load();
  var busy = false;
  var humanAsk = null; // TV1 human: the header button's send-once queue

  function build() {
    var host = document.createElement("div");
    host.setAttribute("data-cobrag-widget", "");
    root = host.attachShadow({ mode: "open" });
    document.body.appendChild(host);

    var pos = script.getAttribute("data-position") || cfg.position;
    var color = script.getAttribute("data-color") || cfg.primary_color;
    if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = "#4f46e5";
    if (pos !== "left") pos = "right";

    // The line under the title that says this is an AI assistant (when the owner shows it).
    // TV5 disclosure: a badge replaces the sub-line (one disclosure, not two)
    var disclosure = cfg.ai_badge
      ? '<p class="sub">' + (CORE.aiBadgeHtml ? CORE.aiBadgeHtml(!!cfg.ai_badge, CORE.getLang()) : "") + "</p>"
      : cfg.ai_disclosure ? '<p class="sub">' + esc(cfg.ai_disclosure) + "</p>" : "";
    var titleText = label("title");
    var placeholderText = label("placeholder");
    var sendText = t("send");
    // TV1 human: the "Talk to a person" button, or nothing
    var humanHtml = CORE.humanButtonHtml ? CORE.humanButtonHtml(cfg.human_button, CORE.getLang()) : "";

    root.innerHTML =
      "<style>" + css(color, pos) + "</style>" +
      '<div class="wrap">' +
      '<section class="panel" role="dialog" aria-label="' + esc(titleText) + '">' +
      '<div class="head"><div><h2>' + esc(titleText) + "</h2>" +
      disclosure +
      "</div><div class=\"actions\">" +
      humanHtml + // TV1 human
      '<button class="icon-btn reset" type="button" aria-label="' + esc(t("newChat")) + '" title="' + esc(t("newChatTitle")) + '">' + RESET_ICON + "</button>" +
      '<button class="icon-btn close" type="button" aria-label="' + esc(t("closeChat")) + '">' + CLOSE_ICON + "</button>" +
      "</div></div>" +
      '<div class="msgs" aria-live="polite"></div>' +
      "<form>" +
      '<textarea rows="1" aria-label="' + esc(t("yourMessage")) + '" placeholder="' + esc(placeholderText) + '"></textarea>' +
      '<button class="send" type="submit">' + esc(sendText) + "</button>" +
      "</form>" +
      footHtml() + // "Powered by" line, or nothing

      "</section>" +
      '<button class="launcher" type="button" aria-label="' + esc(t("openChat")) + '">' + CHAT_ICON + "</button>" +
      "</div>";

    el.panel = root.querySelector(".panel");
    el.msgs = root.querySelector(".msgs");
    el.form = root.querySelector("form");
    el.input = root.querySelector("textarea");
    el.send = root.querySelector(".send");
    el.launcher = root.querySelector(".launcher");
    wirePrivacy(root); // P3 legal
    markLang();

    el.launcher.addEventListener("click", toggle);
    el.msgs.addEventListener("click", function (e) {
      for (var t = e.target; t && t !== el.msgs; t = t.parentNode) {
        if (!t.classList) continue;
        if (t.classList.contains("md-cite")) { showCite(t); return; }
        if (t.classList.contains("chip")) { chipTap(t); return; }
        if (t.classList.contains("cta-x")) { ctaDismiss(t); return; }
        if (t.classList.contains("cta-btn")) { ctaTap(t); return; }
      }
    });
    root.querySelector(".close").addEventListener("click", toggle);
    // TV1 human: the header button asks for a person in the visitor's language (the same ask as the
    // call-to-action); a tap while a reply streams is sent once, when it ends.
    var humanBtn = root.querySelector(".human");
    if (humanBtn && CORE.humanAsk) {
      humanAsk = CORE.humanAsk(function () { return busy; }, function () {
        submit(HANDOFF_ASKS[visitorLang()] || HANDOFF_ASKS.en);
      });
      humanBtn.addEventListener("click", function () { humanAsk.tap(); });
    }
    // /TV1 human
    root.querySelector(".reset").addEventListener("click", function () {
      if (busy) return;
      history = [];
      save(history);
      try { sessionStorage.removeItem(SESSION); } catch (e) {}
      renderAll();
      el.input.focus();
    });
    el.form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    el.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
      if (e.key === "Escape") toggle();
    });
    el.input.addEventListener("input", function () {
      el.input.style.height = "auto";
      el.input.style.height = Math.min(el.input.scrollHeight, 120) + "px";
    });

    renderAll();
    scheduleProactive();
    if (script.getAttribute("data-open") === "true") toggle();
    // LP2 landing: data-ask pre-fills the input once and never sends it (the visitor sends their own
    // question); a page that loaded the widget on a click re-opens it through __cobRagWidgetOpen.
    var ask = (script.getAttribute("data-ask") || "").slice(0, 200);
    if (ask) {
      el.input.value = ask;
      el.input.dispatchEvent(new Event("input"));
    }
    window.__cobRagWidgetOpen = function () {
      if (!el.panel.classList.contains("open")) toggle();
      else el.input.focus();
    };
    // /LP2 landing
  }

  // A title, greeting or placeholder the owner left at the engine's English default is shown in the
  // visitor's language; one the owner wrote is shown as written.
  function label(key) {
    var value = cfg[key];
    return CORE.DEFAULTS_EN && value === CORE.DEFAULTS_EN[key] ? t(key) : value;
  }

  // The visitor's language changed (the first reply says which language they write in): relabel the
  // chrome in place, without rebuilding the panel (a draft message, an open form and focus stay).
  // The panel's chrome is in the visitor's language: say so, so a screen reader speaks it right (WCAG 3.1.2).
  function markLang() {
    var lang = CORE.getLang();
    el.panel.setAttribute("lang", lang);
    el.launcher.setAttribute("lang", lang);
  }

  function relabel() {
    if (!el.panel) return;
    markLang();
    var title = label("title");
    el.panel.setAttribute("aria-label", title);
    var h2 = root.querySelector("h2");
    if (h2) h2.textContent = title;
    el.send.textContent = t("send");
    el.input.setAttribute("placeholder", label("placeholder"));
    el.input.setAttribute("aria-label", t("yourMessage"));
    var reset = root.querySelector(".reset");
    reset.setAttribute("aria-label", t("newChat"));
    reset.setAttribute("title", t("newChatTitle"));
    root.querySelector(".close").setAttribute("aria-label", t("closeChat"));
    var badge = root.querySelector(".ai-badge"); // TV5 disclosure
    if (badge) badge.textContent = t("aiBadge");
    var human = root.querySelector(".human"); // TV1 human
    if (human) {
      human.textContent = t("talkToPerson");
      human.setAttribute("aria-label", t("talkToPerson"));
      human.setAttribute("title", t("talkToPerson"));
    }
    el.launcher.setAttribute("aria-label", t(el.panel.classList.contains("open") ? "closeChat" : "openChat"));
  }

  function toggle() {
    var open = el.panel.classList.toggle("open");
    el.launcher.setAttribute("aria-label", t(open ? "closeChat" : "openChat"));
    if (open) { hideNudge(); el.input.focus(); scroll(); }
  }

  function scroll() { el.msgs.scrollTop = el.msgs.scrollHeight; }

  function bubble(role, html) {
    var div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = html;
    el.msgs.appendChild(div);
    scroll();
    return div;
  }

  // The source list under an answer, built as DOM nodes (file names come from documents).
  function sourcesEl(answer, sources) {
    if (!cfg.show_sources || !sources || !sources.length) return null;
    var seen = {};
    var wrap = document.createElement("div");
    wrap.className = "sources";
    citedSources(answer, sources).forEach(function (s) {
      var label = (s.file_name || t("document")) + (s.page ? " · p." + s.page : "") + updatedPart(s);
      if (seen[label]) return;
      seen[label] = true;
      var chip = document.createElement("span");
      chip.className = "src";
      chip.title = s.snippet || "";
      chip.textContent = label;
      wrap.appendChild(chip);
    });
    return wrap;
  }

  // TV5 freshness: " · Updated 3 Oct" when the engine knows when the source last changed
  function updatedPart(s) {
    var label = CORE.updatedLabel ? CORE.updatedLabel(s.updated_at, visitorLang()) : "";
    return label ? " · " + label : "";
  }
  // /TV5 freshness

  // Only what a chip needs, so a long chat fits in sessionStorage.
  function slimSources(sources) {
    return (sources || []).map(function (s) {
      return { index: s.index, file_name: s.file_name, section: s.section, page: s.page, snippet: s.snippet, updated_at: s.updated_at };
    });
  }

  // A tap on a citation chip opens that source's name and passage under the answer; again closes it.
  function showCite(btn) {
    var msg = btn;
    while (msg && !(msg.classList && msg.classList.contains("msg"))) msg = msg.parentNode;
    if (!msg) return;
    var n = String(btn.getAttribute("data-cite"));
    var src = null;
    (msg._sources || []).forEach(function (s) { if (String(s.index) === n) src = s; });
    var old = msg.querySelector(".cite-pop");
    if (old) {
      var same = old.getAttribute("data-cite") === n;
      old.parentNode.removeChild(old);
      if (same) return;
    }
    if (!src) return;
    var pop = document.createElement("div");
    pop.className = "cite-pop";
    pop.setAttribute("data-cite", n);
    pop.setAttribute("role", "note");
    var head = document.createElement("b");
    head.textContent = "[" + n + "] " + (src.file_name || t("document")) + (src.page ? " · p." + src.page : "") + updatedPart(src);
    pop.appendChild(head);
    if (src.snippet) {
      var body = document.createElement("p");
      body.textContent = src.snippet;
      pop.appendChild(body);
    }
    msg.insertBefore(pop, msg.querySelector(".sources"));
  }

  // Sources the answer cites ([1], [2, 3]) come first, then the best remaining
  // retrieved ones, three at most. Not "cited only": small models sometimes cite
  // the wrong number, and a confidently wrong chip is worse than a broad one.
  function citedSources(answer, sources) {
    var cited = {};
    (String(answer).match(/\[\^?\d+(?:\s*[,–-]\s*\d+)*\]|【\s*\d+\s*】/g) || []).forEach(function (m) {
      (m.match(/\d+/g) || []).forEach(function (n) { cited[n] = true; });
    });
    var first = sources.filter(function (s) { return s.index != null && cited[String(s.index)]; });
    var rest = sources.filter(function (s) { return first.indexOf(s) < 0; });
    var seen = {};
    return first.concat(rest).filter(function (s) {
      var file = s.file_name || "";
      if (seen[file]) return false;
      seen[file] = true;
      return true;
    }).slice(0, 3);
  }

  function renderAll() {
    hideNudge();
    el.msgs.innerHTML = "";
    // TV5 disclosure: the first bot line says it is an AI, above the greeting, in every new chat
    var discLine = (cfg.disclosure_lines || {})[visitorLang()] || cfg.disclosure_line;
    if (discLine) mdInto(bubble("bot disclosure", ""), discLine, []);
    mdInto(bubble("bot", ""), label("greeting"), []);
    history.forEach(function (turn) {
      if (turn.role === "user") bubble("user", esc(turn.content).replace(/\n/g, "<br>"));
      else {
        var b = bubble("bot", "");
        mdInto(b, turn.content, turn.sources);
        var list = sourcesEl(turn.content, turn.sources);
        if (list) b.appendChild(list);
        if (turn.ui && turn.ui.kind === "handoff_card") renderHandoffCard(turn.ui, b);
      }
    });
    // The owner's starters under the greeting until the visitor sends a first message.
    if (!history.length) {
      var starters = chipsEl(cfg.starters);
      if (starters) el.msgs.appendChild(starters);
    }
  }

  // ------------------------------------------- suggestion chips and one call to action
  // Both are built as DOM nodes: labels come from the owner's settings or from the model, and
  // only `textContent` ever carries them. A link goes through CobMd.safeUrl, so a link that is
  // not http(s), mailto or tel is dropped rather than shown.
  var DISMISS = { en: "Not now", hi: "अभी नहीं", "hi-Latn": "Abhi nahi" };
  var SUGGEST = { en: "Suggestions", hi: "सुझाव", "hi-Latn": "Sujhav" };
  var HANDOFF_ASK = "I'd like to talk to a person";
  var pendingChip = false; // a starter tapped before the first reply has no chat session yet

  function chipsEl(items) {
    var wrap = document.createElement("div");
    wrap.className = "chips";
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", SUGGEST[visitorLang()] || SUGGEST.en);
    (items || []).slice(0, 4).forEach(function (q) {
      if (!q || !q.label || !q.message) return;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.textContent = String(q.label);
      b._message = String(q.message);
      wrap.appendChild(b);
    });
    return wrap.firstChild ? wrap : null;
  }

  function ctaEl(cta) {
    if (!cta || !cta.type || !cta.label) return null;
    var wrap = document.createElement("div");
    wrap.className = "cta";
    var btn;
    if (cta.type === "lead_form" || cta.type === "handoff") {
      btn = document.createElement("button");
      btn.type = "button";
    } else {
      var href = MD.safeUrl ? MD.safeUrl(cta.url || "") : null;
      if (!href) return null; // never a broken or unsafe button
      btn = document.createElement("a");
      btn.href = href;
      btn.target = "_blank";
      btn.rel = "noopener noreferrer nofollow";
    }
    btn.className = "cta-btn";
    btn.setAttribute("data-cta", String(cta.type));
    btn.textContent = String(cta.label);
    var no = document.createElement("button");
    no.type = "button";
    no.className = "cta-x";
    no.setAttribute("data-cta", String(cta.type));
    no.textContent = DISMISS[visitorLang()] || DISMISS.en;
    no.setAttribute("aria-label", no.textContent + ": " + String(cta.label));
    wrap.appendChild(btn);
    wrap.appendChild(no);
    return wrap;
  }

  function clearOffers() {
    var old = el.msgs.querySelectorAll(".chips,.cta");
    for (var i = 0; i < old.length; i++) old[i].parentNode.removeChild(old[i]);
  }

  // Taps are counted by the engine (best effort: a failed call never blocks the visitor).
  function sendEvent(type, ctaType) {
    var token = getSession();
    if (!token) { if (type === "chip_click") pendingChip = true; return; }
    var body = { session: token, type: type };
    if (ctaType) body.cta_type = ctaType;
    try {
      fetch(API + "/api/v1/widget/event", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Widget-Key": KEY },
        body: JSON.stringify(body),
      }).catch(function () {});
    } catch (e) {}
  }

  function chipTap(chip) {
    if (busy || !chip._message) return;
    sendEvent("chip_click");
    submit(chip._message); // a chip is a shortcut for typing: the same path as a typed message
  }

  function ctaTap(btn) {
    var type = btn.getAttribute("data-cta");
    sendEvent("cta_click", type);
    if (type === "lead_form") { if (typeof openLeadForm === "function") openLeadForm(null); }
    else if (type === "handoff") {
      if (typeof openHandoff === "function") openHandoff();
      else if (!busy) submit(HANDOFF_ASK);
    }
    // A link is a normal anchor: the browser opens it in a new tab.
  }

  function ctaDismiss(btn) {
    sendEvent("cta_dismiss", btn.getAttribute("data-cta"));
    var wrap = btn.parentNode;
    if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap);
  }

  // ------------------------------------------------------ talk to a person (handoff)
  // The assistant offers ways to reach the business (a `ui` event of kind handoff_card). A tap on a
  // link is reported once so the business is told; the link opens in its own tab. A call-back opens
  // the lead form in its callback variant (renderLeadForm, `block.callback`).
  var HANDOFF_ASKS = {
    en: "I'd like to talk to a person",
    hi: "मुझे किसी व्यक्ति से बात करनी है",
    "hi-Latn": "Mujhe kisi insaan se baat karni hai"
  };

  function reportHandoff(channel) {
    var token = getSession();
    if (!token) return;
    try {
      fetch(API + "/api/v1/widget/handoff", {
        method: "POST",
        keepalive: true, // the link may leave the page; the report still goes out
        headers: { "Content-Type": "application/json", "X-Widget-Key": KEY },
        body: JSON.stringify({ session: token, channel: channel }),
      }).catch(function () {});
    } catch (e) { /* reporting never blocks the visitor */ }
  }

  function renderHandoffCard(block, bubbleEl) {
    if (!window.CobHandoff) return;
    var holder = document.createElement("div");
    holder.innerHTML = window.CobHandoff.cardHtml(block, visitorLang());
    var card = holder.firstChild;
    if (!card) return;
    card.addEventListener("click", function (e) {
      var target = e.target && e.target.closest ? e.target.closest(".chan") : null;
      if (!target) return;
      var kind = target.getAttribute("data-kind");
      if (kind === "callback") {
        e.preventDefault();
        if (block.callback_form) openLeadForm(block.callback_form);
        return;
      }
      reportHandoff(kind);
    });
    bubbleEl.appendChild(card);
    scroll();
  }

  // The "talk to a person" button of a call-to-action: asks in the visitor's language, so the
  // assistant offers the card the same way as when they type it.
  function openHandoff() {
    if (busy) return;
    submit(HANDOFF_ASKS[visitorLang()] || HANDOFF_ASKS.en);
  }

  // --------------------------------------------------------- proactive greeting
  // One small bubble per page view, from the owner's list (the first greeting that fits this page and
  // has not used up its frequency). Never over an open panel, never after the visitor has written.
  var sentThisView = false;
  var nudgeShown = false;
  var nudgeTimer = null;
  var nudgeEl = null;

  function nudgeStorage() {
    var s = { session: null, local: null };
    try { s.session = window.sessionStorage; } catch (e) {}
    try { s.local = window.localStorage; } catch (e) {}
    // A blocked store reads as "not shown yet"; the in-memory flag above still caps it at one per page view.
    var none = { getItem: function () { return null; }, setItem: function () {} };
    return { session: s.session || none, local: s.local || none };
  }

  // "Once a day" is the visitor's calendar day, not the UTC one (which turns over at 05:30 in India).
  function localDay() {
    var d = new Date();
    return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2);
  }

  function scheduleProactive() {
    var list = cfg.proactive || [];
    var storage = nudgeStorage();
    for (var i = 0; i < list.length; i++) {
      var g = list[i];
      var due = false;
      try { due = CORE.proactiveDue(g, String(location.href), storage, localDay()); } catch (e) { due = false; }
      if (!due) continue;
      var wait = Math.max(0, Number(g.delay_seconds) || 0) * 1000;
      nudgeTimer = setTimeout(function (chosen) { return function () { showNudge(chosen, storage); }; }(g), wait);
      return; // one greeting per page view
    }
  }

  function hideNudge() {
    if (nudgeTimer) { clearTimeout(nudgeTimer); nudgeTimer = null; }
    if (nudgeEl && nudgeEl.parentNode) nudgeEl.parentNode.removeChild(nudgeEl);
    nudgeEl = null;
  }

  function showNudge(g, storage) {
    nudgeTimer = null;
    if (nudgeShown || sentThisView || !g.message || el.panel.classList.contains("open")) return;
    nudgeShown = true;
    CORE.markShown(g, storage, localDay());
    var box = document.createElement("div");
    box.className = "nudge";
    box.setAttribute("role", "status");
    var text = document.createElement("div");
    text.className = "md";
    box.appendChild(text);
    MD.stream(text, {}).update(String(g.message), true); // owner text: rendered by the safe Markdown renderer
    var close = document.createElement("button");
    close.type = "button";
    close.className = "x";
    close.setAttribute("aria-label", t("close"));
    close.innerHTML = CLOSE_ICON; // a constant, no text from anywhere
    var off = document.createElement("button");
    off.type = "button";
    off.className = "off";
    off.textContent = t("dontShowAgain");
    // Mouse users click the message; this button is the same action for keyboard and switch users.
    var go = document.createElement("button");
    go.type = "button";
    go.className = "go";
    go.textContent = t("openChat");
    box.appendChild(go);
    box.appendChild(close);
    box.appendChild(off);
    box.addEventListener("click", function (e) {
      var target = e.target;
      if (target && target.closest && target.closest(".off")) { CORE.dismissForever(g, storage); hideNudge(); return; }
      if (target && target.closest && target.closest(".x")) { hideNudge(); return; }
      if (target && target.closest && target.closest("a")) return; // a link in the message opens as usual
      hideNudge();
      if (!el.panel.classList.contains("open")) toggle();
    });
    nudgeEl = box;
    el.launcher.parentNode.insertBefore(box, el.launcher);
  }

  // --------------------------------------------------------------- network
  // Where the visitor is and how they arrived, for the assistant's context (never the hash).
  function pageUrl() {
    try { return String(location.href).split("#")[0].slice(0, 2048) || null; } catch (e) { return null; }
  }
  function utmParams() {
    var out = {}, n = 0;
    try {
      String(location.search).replace(/^\?/, "").split("&").forEach(function (pair) {
        var kv = pair.split("="), k = decodeURIComponent(kv[0] || "");
        if (n < 5 && /^utm_[a-z]{1,30}$/.test(k)) { out[k] = decodeURIComponent(kv[1] || "").slice(0, 200); n++; }
      });
    } catch (e) {}
    return n ? out : null;
  }
  function getSession() { try { return sessionStorage.getItem(SESSION); } catch (e) { return null; } }
  function setSession(token) { try { if (token) sessionStorage.setItem(SESSION, token); } catch (e) {} }

  // `forced` is a message sent for the visitor (the call-back button); the typed text stays untouched.
  function submit(forced) {
    var fromButton = typeof forced === "string";
    var text = (fromButton ? forced : el.input.value).trim();
    if (!text || busy) return;
    clearOffers(); // last answer's chips and button give way to this message
    sentThisView = true; // a visitor who is already chatting gets no greeting
    hideNudge();
    busy = true;
    el.send.disabled = true;
    if (!fromButton) {
      el.input.value = "";
      el.input.style.height = "auto";
    }

    var prior = history.map(function (t) { return { role: t.role, content: t.content }; });
    history.push({ role: "user", content: text });
    save(history);
    bubble("user", esc(text).replace(/\n/g, "<br>"));
    var out = bubble("bot", '<span class="wait"><span class="typing"><i></i><i></i><i></i></span><span class="stage" aria-live="polite"></span></span>');

    var answer = "";
    var sources = [];
    var mdLive = null; // the incremental Markdown view of this answer, once tokens arrive
    var pendingUi = null; // a lead form the assistant offers once the answer is complete
    var chips = []; // `quick_replies`: up to four suggestion chips for after the answer
    var cta = null; // `done.cta`: at most one call-to-action button
    // A wait longer than STAGE_AFTER_MS says what the assistant is doing, in plain words.
    var sentAt = Date.now();
    var stage = "";
    function showStage() {
      if (answer || !stage || Date.now() - sentAt < STAGE_AFTER_MS) return;
      var words = t("stage_" + stage);
      if (words === "stage_" + stage) words = ""; // a stage this widget has no words for
      var slot = out.querySelector(".stage");
      if (words && slot && slot.textContent !== words) slot.textContent = words;
    }
    var stageTimer = setInterval(showStage, 500);

    fetch(API + "/api/v1/widget/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Widget-Key": KEY },
      // `history` is still sent: an engine whose assistant is switched off answers from it.
      body: JSON.stringify({ message: text, history: prior.slice(-MAX_HISTORY), session: getSession(), page_url: pageUrl(), utm: utmParams() }),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().catch(function () { return {}; }).then(function (body) {
            var e = new Error(body.detail || t("errorStatus", { s: res.status }));
            // Not available right now / monthly limit: a polite reply, not a red error.
            e.polite = !!body.detail && POLITE_CODES.indexOf(body.code) !== -1;
            throw e;
          });
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var buffer = "";
        function pump() {
          return reader.read().then(function (chunk) {
            if (chunk.done) return;
            buffer += decoder.decode(chunk.value, { stream: true });
            var events = buffer.split("\n\n");
            buffer = events.pop();
            events.forEach(function (raw) {
              var type = "message", data = "";
              raw.split("\n").forEach(function (line) {
                if (line.indexOf("event:") === 0) type = line.slice(6).trim();
                else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
              });
              var payload = {};
              try { payload = data ? JSON.parse(data) : {}; } catch (e) { return; }
              if (type === "meta") {
                setSession(payload.session);
                if (payload.language) lastLang = String(payload.language);
                CORE.setLang(CORE.pickLanguage(payload.language, cfg.language, navigator.language));
                relabel();
                // Devanagari text gets the roomier line height; Hinglish is Latin script.
                out.setAttribute("lang", payload.language === "hi" ? "hi" : "en");
              } else if (type === "ui") {
                if (payload.kind === "lead_form" || payload.kind === "handoff_card") pendingUi = payload;
              } else if (type === "stage") {
                stage = String(payload.stage || "");
                showStage();
              } else if (type === "sources") sources = payload.sources || [];
              else if (type === "token") {
                answer += payload.content || "";
                if (!mdLive) {
                  // First words: the typing indicator gives way to the answer, which then only grows.
                  while (out.firstChild) out.removeChild(out.firstChild);
                  var box = document.createElement("div");
                  box.className = "md";
                  out.appendChild(box);
                  out._sources = sources;
                  mdLive = MD.stream(box, mdOpts(sources));
                }
                mdLive.update(visibleText(answer), false);
                scroll();
              } else if (type === "quick_replies") chips = payload.items || [];
              else if (type === "done") cta = payload.cta || null;
              else if (type === "error") {
                var streamErr = new Error(payload.detail || t("cantAnswer"));
                // A demo's daily limit arrives as a sentence to show as a normal reply.
                streamErr.polite = !!payload.detail && POLITE_CODES.indexOf(payload.code) !== -1;
                throw streamErr;
              }
            });
            return pump();
          });
        }
        return pump();
      })
      .then(function () {
        clearInterval(stageTimer);
        if (!answer) answer = t("noAnswer");
        sources = slimSources(sources);
        if (mdLive) mdLive.update(visibleText(answer), true); // the last block stops being provisional
        else mdInto(out, answer, sources);
        var list = sourcesEl(answer, sources);
        if (list) out.appendChild(list);
        var turn = { role: "assistant", content: answer, sources: sources };
        if (pendingUi && pendingUi.kind === "handoff_card") turn.ui = pendingUi; // shown again after a reload
        history.push(turn);
        save(history);
        // After the answer, never instead of it.
        if (pendingUi && pendingUi.kind === "handoff_card") renderHandoffCard(pendingUi, out);
        else if (pendingUi) openLeadForm(pendingUi);
        // The button and the chips sit under the answer (and under a form, which excludes the button).
        var offer = ctaEl(cta);
        if (offer) el.msgs.appendChild(offer);
        var more = chipsEl(chips);
        if (more) el.msgs.appendChild(more);
        if (pendingChip) { pendingChip = false; sendEvent("chip_click"); }
      })
      .catch(function (err) {
        clearInterval(stageTimer);
        out.remove();
        history.pop(); // the question was not answered; do not replay it as context
        save(history);
        if (err.polite) bubble("bot", esc(err.message));
        else bubble("err", esc(err.message || t("network")));
      })
      .then(function () {
        busy = false;
        el.send.disabled = false;
        el.input.focus();
        scroll();
        if (humanAsk) humanAsk.done(); // TV1 human: a tap made while this reply streamed
      });
  }


  // ------------------------------------------------------------- lead form
  // The assistant offers a form after an answer (a `ui` event of kind lead_form). It is built from DOM
  // nodes; the only HTML is the owner's consent notice, which CobMd.inline escapes and links safely.
  // Pure helpers (validation, wording) live in lead_form.js (window.CobLeadForm), served in front.
  var LEAD = window.CobLeadForm;
  var lastLang = null; // the language of the last reply (`meta`), else the assistant's default
  var formSeq = 0;

  function visitorLang() { return lastLang || cfg.language || "en"; }

  // Shows a form block, or (with null, e.g. a "call me back" button) asks the assistant for one in
  // the visitor's language; the director then decides and offers it. Never opens a form on its own.
  function openLeadForm(block) {
    if (!LEAD) return;
    if (block) { renderLeadForm(block, visitorLang()); return; }
    if (busy) return;
    submit(LEAD.callbackMessage(visitorLang()));
  }

  function todayLocal() {
    var d = new Date();
    function two(n) { return (n < 10 ? "0" : "") + n; }
    return d.getFullYear() + "-" + two(d.getMonth() + 1) + "-" + two(d.getDate());
  }
  function withWindow(body, callbackWindow) {
    if (callbackWindow) body.callback_window = callbackWindow;
    return body;
  }

  function renderLeadForm(block, lang) {
    var m = LEAD.messages(lang);
    var old = el.msgs.querySelectorAll(".lead");
    for (var o = 0; o < old.length; o++) old[o].parentNode.removeChild(old[o]);
    var uid = "lf" + (++formSeq);

    var card = document.createElement("div");
    card.className = "lead";
    card.setAttribute("role", "group");
    card.setAttribute("aria-label", block.title);
    var title = document.createElement("h3");
    title.textContent = block.title;
    card.appendChild(title);

    var notice = document.createElement("p");
    notice.className = "notice";
    // Owner text, escaped and link-checked by the renderer (never raw HTML).
    if (MD.inline) notice.innerHTML = MD.inline(block.notice);
    else notice.textContent = block.notice;
    card.appendChild(notice);

    var form = document.createElement("form");
    form.noValidate = true;
    var inputs = {};
    var errs = {};
    function fieldError(id, text) {
      var slot = errs[id];
      var input = inputs[id];
      if (!slot || !input) return;
      slot.textContent = text || "";
      if (text) input.setAttribute("aria-invalid", "true");
      else input.removeAttribute("aria-invalid");
    }

    block.fields.forEach(function (f) {
      var id = uid + "-" + f.id;
      var label = document.createElement("label");
      label.setAttribute("for", id);
      label.textContent = f.label + (f.required ? " *" : "");
      var input;
      if (f.type === "select") {
        input = document.createElement("select");
        var blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "—";
        input.appendChild(blank);
        (f.options || []).forEach(function (opt) {
          var o = document.createElement("option");
          o.value = opt;
          o.textContent = opt;
          input.appendChild(o);
        });
      } else {
        input = document.createElement("input");
        input.type = f.type === "tel" || f.type === "email" || f.type === "date" || f.type === "number" ? f.type : "text";
        if (f.type === "tel") input.setAttribute("inputmode", "tel");
        if (f.type === "number") input.setAttribute("inputmode", "decimal");
        var auto = { name: "name", phone: "tel", email: "email" }[f.id];
        input.autocomplete = auto || "off";
      }
      input.id = id;
      input.name = f.id;
      if (block.callback && f.id === "callback_date") input.min = todayLocal(); // no day that has passed
      if (f.required) input.setAttribute("aria-required", "true");
      var err = document.createElement("span");
      err.className = "ferr";
      err.id = id + "-err";
      err.setAttribute("role", "alert");
      input.setAttribute("aria-describedby", err.id);
      inputs[f.id] = input;
      errs[f.id] = err;
      form.appendChild(label);
      form.appendChild(input);
      form.appendChild(err);
    });

    // Spam trap: off-screen, out of the tab order, empty for people. A bot that fills it is ignored.
    var trap = document.createElement("div");
    trap.className = "hp";
    trap.setAttribute("aria-hidden", "true");
    var trapLabel = document.createElement("label");
    trapLabel.textContent = m.website;
    var trapInput = document.createElement("input");
    trapInput.type = "text";
    trapInput.name = "website";
    trapInput.tabIndex = -1;
    trapInput.autocomplete = "off";
    trapLabel.appendChild(trapInput);
    trap.appendChild(trapLabel);
    form.appendChild(trap);

    var consentLabel = document.createElement("label");
    consentLabel.className = "consent";
    var consent = document.createElement("input");
    consent.type = "checkbox";
    consent.id = uid + "-consent";
    consent.checked = false; // never pre-ticked
    var consentText = document.createElement("span");
    consentText.textContent = block.consent_label;
    consentLabel.appendChild(consent);
    consentLabel.appendChild(consentText);
    var consentErr = document.createElement("span");
    consentErr.className = "ferr";
    consentErr.setAttribute("role", "alert");
    consent.setAttribute("aria-describedby", uid + "-consent-err");
    consentErr.id = uid + "-consent-err";
    form.appendChild(consentLabel);
    form.appendChild(consentErr);

    var send = document.createElement("button");
    send.type = "submit";
    send.textContent = block.submit_label;
    form.appendChild(send);
    var status = document.createElement("p");
    status.className = "lstatus";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    form.appendChild(status);
    card.appendChild(form);

    var sending = false;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (sending) return;
      var values = {};
      block.fields.forEach(function (f) { values[f.id] = inputs[f.id].value; fieldError(f.id, ""); });
      consentErr.textContent = "";
      status.textContent = "";
      var checked = LEAD.collect(block.fields, values, lang);
      var firstBad = null;
      block.fields.forEach(function (f) {
        if (checked.errors[f.id]) { fieldError(f.id, checked.errors[f.id]); firstBad = firstBad || inputs[f.id]; }
      });
      if (!consent.checked) { consentErr.textContent = m.consent; firstBad = firstBad || consent; }
      // A call-back request: the day and window travel as `callback_window`, not as form fields.
      var callbackWindow = null;
      if (block.callback && !firstBad) {
        callbackWindow = window.CobHandoff ? window.CobHandoff.callbackWindow(checked.payload.callback_date, checked.payload.callback_window) : null;
        if (!callbackWindow) { fieldError("callback_window", m.callback); firstBad = inputs.callback_window; }
      }
      if (firstBad) { firstBad.focus(); return; }
      if (block.callback) { delete checked.payload.callback_date; delete checked.payload.callback_window; }

      sending = true;
      send.disabled = true;
      status.textContent = m.sending;
      fetch(API + "/api/v1/widget/lead", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Widget-Key": KEY },
        body: JSON.stringify(withWindow({ session: getSession(), form_id: block.form_id, fields: checked.payload, consent: true, website: trapInput.value }, callbackWindow)),
      })
        .then(function (res) {
          if (res.status === 201) return res.json().then(function (body) { thanks(card, body.confirmation || {}, m); });
          return res.json().catch(function () { return {}; }).then(function (body) {
            sending = false;
            send.disabled = false;
            status.textContent = "";
            if (res.status === 422 && body.code === "lead_consent_required") consentErr.textContent = m.consent;
            else if (res.status === 422 && body.code === "callback_window_invalid") fieldError("callback_window", m.callback);
            else if (res.status === 422 && body.field && inputs[body.field]) {
              var own = { lead_invalid_phone: m.phone, lead_invalid_email: m.email }[body.code];
              // The server's own text is English: an unmapped code gets the visitor's language.
              fieldError(body.field, own || m.required);
              inputs[body.field].focus();
            } else if (res.status === 400 && body.code === "widget_session_invalid") status.textContent = m.expired;
            else if (res.status === 429) status.textContent = m.tooMany; // retrying at once only hits the limit again
            else status.textContent = m.failed; // 5xx, anything else: calm and generic
          });
        })
        .catch(function () {
          sending = false;
          send.disabled = false;
          status.textContent = m.failed;
        });
    });

    el.msgs.appendChild(card);
    var first = form.querySelector("input,select");
    if (first) first.focus();
    scroll();
  }

  // The form gives way to the confirmation and the visitor's way to withdraw consent.
  function thanks(card, confirmation, m) {
    while (card.firstChild) card.removeChild(card.firstChild);
    var p = document.createElement("p");
    p.className = "thanks";
    p.setAttribute("role", "status");
    p.textContent = confirmation.message || "";
    card.appendChild(p);
    var url = MD.safeUrl ? MD.safeUrl(confirmation.withdraw_url || "") : null;
    if (url) {
      var a = document.createElement("a");
      a.className = "withdraw";
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = m.withdraw;
      card.appendChild(a);
    }
    // A qualified lead is sent straight to booking when the owner set a link.
    var book = MD.safeUrl ? MD.safeUrl(confirmation.booking_url || "") : null;
    if (book) {
      var b = document.createElement("a");
      b.className = "book";
      b.href = book;
      b.target = "_blank";
      b.rel = "noopener noreferrer";
      b.textContent = t("bookNow");
      card.insertBefore(b, card.firstChild.nextSibling);
    }
    scroll();
  }

  // ------------------------------------------------------------------ boot
  function start() {
    fetch(API + "/api/v1/widget/config", { headers: { "X-Widget-Key": KEY } })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (remote) {
        if (remote) Object.keys(cfg).forEach(function (k) { if (remote[k] != null) cfg[k] = remote[k]; });
        if (remote && remote.powered_by === null) cfg.powered_by = null; // hidden in Branding
      })
      .catch(function () { /* render with defaults; chat will report the real error */ })
      .then(function () {
        CORE.setLang(CORE.pickLanguage(null, cfg.language, navigator.language));
        // BIZ2 sensor
        if (cfg.sensor && window.CobSensor) window.CobSensor.run(API, KEY, cfg.sensor);
        // /BIZ2 sensor
      })
      .then(build);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
