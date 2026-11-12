/*!
 * cob-rag-engine widget core helpers. (c) 2026 Callobuzz | SPDX-License-Identifier: MIT. Proactive greetings and the widget's language.
 * ES5, no dependencies, no DOM access. Exposes window.CobWidgetCore. Served in front of widget.js.
 */
(function (root) {
  "use strict";

  function escapeRe(c) {
    return /[.+^${}()|[\]\\\/\-]/.test(c) ? "\\" + c : c;
  }

  // The same glob as conversation/page_glob.py (both read tests/fixtures/conversation/page_globs.json):
  // "**" any characters, "*" any characters but "/", "?" one character but "/"; anchored, case-sensitive.
  function globToRegExp(glob) {
    var out = "", i = 0;
    while (i < glob.length) {
      var c = glob.charAt(i);
      if (c === "*" && glob.charAt(i + 1) === "*") { out += "[\\s\\S]*"; i += 2; continue; }
      if (c === "*") out += "[^/]*";
      else if (c === "?") out += "[^/]";
      else out += escapeRe(c);
      i += 1;
    }
    return new RegExp("^" + out + "$");
  }

  // A glob starting with "http" is matched against the whole address; any other against path + query.
  // The #fragment never counts. A string that is not an address has no page.
  function matchesPage(glob, href) {
    var address = String(href).split("#")[0];
    var target = address;
    if (glob.indexOf("http") !== 0) {
      var m = /^[a-z][a-z0-9+.\-]*:\/\/[^\/]+(\/[^#]*)?/i.exec(address);
      target = m ? (m[1] || "/") : "";
    }
    return globToRegExp(glob).test(target);
  }

  function get(store, key) { try { return store.getItem(key); } catch (e) { return null; } }
  function set(store, key, value) { try { store.setItem(key, value); } catch (e) { /* private mode */ } }

  // Storage that throws (private mode, blocked cookies) reads as "not shown yet".
  function proactiveDue(g, href, storage, nowIso) {
    if (!matchesPage(g.page_url_glob, href)) return false;
    if (get(storage.local, "cobrag:pg:" + g.id + ":off") === "1") return false;
    if (g.frequency === "once_per_session") return get(storage.session, "cobrag:pg:" + g.id) !== "1";
    if (g.frequency === "once_per_day") return get(storage.local, "cobrag:pg:" + g.id) !== nowIso.slice(0, 10);
    return true;
  }

  function markShown(g, storage, nowIso) {
    if (g.frequency === "once_per_session") set(storage.session, "cobrag:pg:" + g.id, "1");
    else if (g.frequency === "once_per_day") set(storage.local, "cobrag:pg:" + g.id, nowIso.slice(0, 10));
  }

  function dismissForever(g, storage) { set(storage.local, "cobrag:pg:" + g.id + ":off", "1"); }

  // ---- the widget's language -------------------------------------------------------------
  var LANGS = { en: 1, hi: 1, "hi-Latn": 1 };

  // The reply's language (`meta.language`), then the assistant's primary language, then the browser
  // (Hindi only; browsers have no Hinglish tag), then English.
  function pickLanguage(metaLang, primary, navLang) {
    if (metaLang && LANGS.hasOwnProperty(metaLang)) return metaLang;
    if (primary && LANGS.hasOwnProperty(primary)) return primary;
    if (navLang && /^hi(-|$)/i.test(navLang)) return "hi";
    return "en";
  }

  // The English defaults the engine sends for title, greeting and placeholder (`_DEFAULTS` in
  // api/routes/widget.py, kept equal by tests/unit/test_widget_defaults_in_sync.py). The server always
  // sends a value, so the widget can only tell "the owner set it" from "the default" by equality: only
  // an untouched default is shown in the visitor's language.
  var DEFAULTS_EN = {
    title: "Ask us anything",
    greeting: "Hi! How can I help you today?",
    placeholder: "Type your question\u2026"
  };

  // Chrome only. Lead-form, handoff and call-to-action wording arrives translated from the engine.
  // Wording is impersonal ("is being read"), because the assistant's name, and so its gender, is the
  // owner's choice; "aap" form throughout. "Powered by" is never translated and not in this table.
  var STRINGS = {
    en: {
      send: "Send",
      title: DEFAULTS_EN.title,
      greeting: DEFAULTS_EN.greeting,
      placeholder: DEFAULTS_EN.placeholder,
      yourMessage: "Your message",
      newChat: "Start a new conversation",
      newChatTitle: "New conversation",
      closeChat: "Close chat",
      openChat: "Open chat",
      close: "Close",
      dontShowAgain: "Don't show again",
      document: "Document",
      stage_understand: "Reading your question\u2026",
      stage_search: "Searching your documents\u2026",
      stage_rerank: "Picking the best matches\u2026",
      stage_check: "Double-checking the details\u2026",
      stage_write: "Writing the answer\u2026",
      stage_verify: "Checking the facts\u2026",
      stage_form: "Preparing a form\u2026",
      stage_handoff: "Finding the best way to reach us\u2026",
      // PIP1 lane: the question is waiting for its turn
      stage_queued: "One moment\u2026",
      // /PIP1 lane
      noAnswer: "Sorry, I couldn't find an answer to that.",
      cantAnswer: "The assistant could not answer.",
      errorStatus: "Something went wrong ({s})",
      network: "Network error",
      bookNow: "Book now",
      talkToPerson: "Talk to a person", // TV1 human
      privacy: "Privacy", // P3 legal
      aiBadge: "AI assistant", // TV5 disclosure
      updated: "Updated {date}" // TV5 freshness
    },
    hi: {
      send: "भेजें",
      title: "हमसे कुछ भी पूछें",
      greeting: "नमस्ते! आज आपकी क्या मदद की जाए?",
      placeholder: "अपना सवाल लिखें…",
      yourMessage: "आपका संदेश",
      newChat: "नई बातचीत शुरू करें",
      newChatTitle: "नई बातचीत",
      closeChat: "चैट बंद करें",
      openChat: "चैट खोलें",
      close: "बंद करें",
      dontShowAgain: "फिर न दिखाएँ",
      document: "दस्तावेज़",
      stage_understand: "आपका सवाल पढ़ा जा रहा है…",
      stage_search: "हमारे दस्तावेज़ खोजे जा रहे हैं…",
      stage_rerank: "सबसे सही नतीजे चुने जा रहे हैं…",
      stage_check: "ब्योरे दोबारा जाँचे जा रहे हैं…",
      stage_write: "जवाब लिखा जा रहा है…",
      stage_verify: "तथ्य जाँचे जा रहे हैं…",
      stage_form: "फ़ॉर्म तैयार किया जा रहा है…",
      stage_handoff: "आप तक पहुँचने का सबसे अच्छा तरीका ढूँढा जा रहा है…",
      // PIP1 lane
      stage_queued: "बस एक पल…",
      // /PIP1 lane
      noAnswer: "माफ़ कीजिए, इसका जवाब नहीं मिल सका।",
      cantAnswer: "सहायक जवाब नहीं दे सका।",
      errorStatus: "कुछ गड़बड़ हो गई ({s})",
      network: "नेटवर्क में दिक्कत है",
      bookNow: "अभी बुक करें",
      talkToPerson: "किसी व्यक्ति से बात करें", // TV1 human
      privacy: "गोपनीयता", // P3 legal
      aiBadge: "एआई सहायक", // TV5 disclosure
      updated: "अपडेट: {date}" // TV5 freshness
    },
    "hi-Latn": {
      send: "Bhejein",
      title: "Hum se kuch bhi poochhiye",
      greeting: "Namaste! Aaj aapki kya madad ki jaaye?",
      placeholder: "Apna sawaal likhiye…",
      yourMessage: "Aapka sandesh",
      newChat: "Nayi baatcheet shuru karein",
      newChatTitle: "Nayi baatcheet",
      closeChat: "Chat band karein",
      openChat: "Chat kholein",
      close: "Band karein",
      dontShowAgain: "Phir na dikhayein",
      document: "Document",
      stage_understand: "Aapka sawaal padha ja raha hai…",
      stage_search: "Hamare documents dekhe ja rahe hain…",
      stage_rerank: "Sabse sahi nateeje chune ja rahe hain…",
      stage_check: "Details dobara check kiye ja rahe hain…",
      stage_write: "Jawaab likha ja raha hai…",
      stage_verify: "Facts check kiye ja rahe hain…",
      stage_form: "Form taiyaar kiya ja raha hai…",
      stage_handoff: "Aap tak pahunchne ka sabse accha tareeka dhoondha ja raha hai…",
      // PIP1 lane
      stage_queued: "Bas ek pal…",
      // /PIP1 lane
      noAnswer: "Maaf kijiye, iska jawaab nahi mil saka.",
      cantAnswer: "Assistant jawaab nahi de saka.",
      errorStatus: "Kuch gadbad ho gayi ({s})",
      network: "Network mein dikkat hai",
      bookNow: "Abhi book karein",
      talkToPerson: "Kisi insaan se baat karein", // TV1 human
      privacy: "Privacy", // P3 legal
      aiBadge: "AI assistant", // TV5 disclosure
      updated: "Update: {date}" // TV5 freshness
    }
  };

  function t(lang, key, vars) {
    var table = STRINGS.hasOwnProperty(lang) ? STRINGS[lang] : STRINGS.en;
    var s = table[key] || STRINGS.en[key] || key;
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, function (m, k) { return vars.hasOwnProperty(k) ? String(vars[k]) : m; });
  }

  // TV1 human: the "Talk to a person" button in the panel header. `human_button` (from the config)
  // decides whether it exists at all. It is a plain button (never a link); the widget turns a tap into
  // the visitor's own ask (HANDOFF_ASKS in widget.js), so the card arrives through the same free path
  // as a typed request.
  function humanButtonHtml(show, lang) {
    if (!show) return "";
    var label = t(lang, "talkToPerson").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
    return '<button class="human" type="button" aria-label="' + label + '" title="' + label + '">' + label + "</button>";
  }

  // A tap sends at once when the chat is idle; while a reply streams it waits for the end and is sent
  // once, however often the button is tapped (never two sends).
  function humanAsk(isBusy, send) {
    var queued = false;
    return {
      tap: function () {
        if (isBusy()) { queued = true; return; }
        send();
      },
      done: function () {
        if (queued && !isBusy()) { queued = false; send(); }
      }
    };
  }
  // /TV1 human

  // TV5 disclosure: the "AI assistant" badge next to the name. Fixed chrome in the visitor's
  // language (the owner cannot edit it); a note with text, never only a colour.
  function aiBadgeHtml(show, lang) {
    if (!show) return "";
    var label = t(lang, "aiBadge").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
    return '<span class="ai-badge" role="note">' + label + "</span>";
  }
  // /TV5 disclosure

  // TV5 freshness: "Updated 3 Oct" on a source chip, from the engine's YYYY-MM-DD, with fixed month
  // names (no Intl, so every browser says the same). Anything that is not a date gives "".
  var MONTHS = {
    en: ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    hi: ["जन॰", "फ़र॰", "मार्च", "अप्रैल", "मई", "जून", "जुला॰", "अग॰", "सित॰", "अक्टू॰", "नव॰", "दिस॰"],
    "hi-Latn": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
  };
  function updatedLabel(iso, lang) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    if (!m) return "";
    var month = parseInt(m[2], 10);
    if (month < 1 || month > 12) return "";
    var names = MONTHS.hasOwnProperty(lang) ? MONTHS[lang] : MONTHS.en;
    return t(lang, "updated", { date: parseInt(m[3], 10) + " " + names[month - 1] });
  }
  // /TV5 freshness

  // The current language, shared by every part served in front of widget.js (lead_form.js, handoff.js).
  var current = "en";
  function setLang(lang) { current = LANGS.hasOwnProperty(lang) ? lang : "en"; }
  function getLang() { return current; }
  function tr(key, vars) { return t(current, key, vars); }

  root.CobWidgetCore = {
    globToRegExp: globToRegExp,
    matchesPage: matchesPage,
    proactiveDue: proactiveDue,
    markShown: markShown,
    dismissForever: dismissForever,
    pickLanguage: pickLanguage,
    STRINGS: STRINGS,
    DEFAULTS_EN: DEFAULTS_EN,
    t: t,
    setLang: setLang,
    getLang: getLang,
    tr: tr,
    humanButtonHtml: humanButtonHtml, // TV1 human
    humanAsk: humanAsk, // TV1 human
    aiBadgeHtml: aiBadgeHtml, // TV5 disclosure
    updatedLabel: updatedLabel // TV5 freshness
  };
})(typeof window !== "undefined" ? window : this);
