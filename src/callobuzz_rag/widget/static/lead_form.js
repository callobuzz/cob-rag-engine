/*! cob-rag-engine widget: lead form helpers. (c) 2026 Callobuzz | SPDX-License-Identifier: MIT. Pure functions, no DOM, ES5.
 * The server validates everything again; this is only for fast, kind feedback. */
(function (root) {
  "use strict";
  var MSG = {
    en: {
      required: "Please fill this in.",
      phone: "Enter a 10-digit mobile number.",
      email: "Enter a valid e-mail.",
      number: "Enter a number.",
      date: "Pick a date.",
      consent: "Please tick the box to agree.",
      sending: "Sending…",
      failed: "Could not send. Please try again.",
      tooMany: "Too many tries. Please try again in a while.",
      expired: "This form has expired. Please ask again.",
      withdraw: "Changed your mind? Withdraw consent",
      website: "Website",
      callback: "Pick a day and a time slot that has not passed."
    },
    hi: {
      required: "कृपया यह भरें।",
      phone: "10 अंकों का मोबाइल नंबर लिखें।",
      email: "सही ई-मेल लिखें।",
      number: "संख्या लिखें।",
      date: "तारीख चुनें।",
      consent: "सहमति के लिए बॉक्स पर टिक करें।",
      sending: "भेजा जा रहा है…",
      failed: "भेजा नहीं जा सका। फिर कोशिश करें।",
      tooMany: "बहुत ज़्यादा कोशिशें हो गईं। कृपया थोड़ी देर बाद फिर कोशिश करें।",
      expired: "यह फ़ॉर्म पुराना हो गया है। कृपया फिर पूछें।",
      withdraw: "मन बदल गया? सहमति वापस लें",
      website: "Website",
      callback: "ऐसा दिन और समय चुनें जो बीत न चुका हो।"
    },
    "hi-Latn": {
      required: "Kripya yeh bhariye.",
      phone: "10 digit ka mobile number likhiye.",
      email: "Sahi email likhiye.",
      number: "Ek number likhiye.",
      date: "Tareekh chuniye.",
      consent: "Sahmati ke liye box par tick kijiye.",
      sending: "Bhej rahe hain…",
      failed: "Bhej nahi paaye. Phir koshish kijiye.",
      tooMany: "Bahut zyada koshishein ho gayi. Kripya thodi der baad phir koshish kijiye.",
      expired: "Yeh form purana ho gaya hai. Kripya phir poochhiye.",
      withdraw: "Mann badal gaya? Sahmati wapas lijiye",
      website: "Website",
      callback: "Aisa din aur samay chuniye jo beet na chuka ho."
    }
  };

  function messages(lang) {
    return MSG[lang] || MSG.en;
  }

  // Indian mobiles (with or without 0 / 91 / +91, 6-9 first) and +<country> numbers of 8-15 digits.
  function validatePhone(raw) {
    var v = String(raw || "").replace(/[\s\-().]/g, "");
    var intl = v.charAt(0) === "+";
    var d = intl ? v.slice(1) : v;
    if (!/^\d+$/.test(d)) return { ok: false };
    if (!intl && d.length === 11 && d.charAt(0) === "0") d = d.slice(1);
    if (!intl && d.length === 12 && d.slice(0, 2) === "91") d = d.slice(2);
    if (!intl && d.length === 10) d = "91" + d;
    if (d.slice(0, 2) === "91" && d.length === 12) {
      return /[6-9]/.test(d.charAt(2)) ? { ok: true, e164: "+" + d } : { ok: false };
    }
    if (intl && d.length >= 8 && d.length <= 15 && d.charAt(0) !== "0") {
      return { ok: true, e164: "+" + d };
    }
    return { ok: false };
  }

  function validateEmail(raw) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(raw || "").trim());
  }

  // fields: the block's field list; values: {id: text}. Returns {errors: {id: message}, payload: {id: value}}.
  function collect(fields, values, lang) {
    var m = messages(lang), errors = {}, payload = {};
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      var v = String(values[f.id] == null ? "" : values[f.id]).trim();
      if (!v) {
        if (f.required) errors[f.id] = m.required;
        continue;
      }
      if (f.type === "tel" && !validatePhone(v).ok) { errors[f.id] = m.phone; continue; }
      if (f.type === "email" && !validateEmail(v)) { errors[f.id] = m.email; continue; }
      if (f.type === "number" && !isFinite(Number(v))) { errors[f.id] = m.number; continue; }
      if (f.type === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(v)) { errors[f.id] = m.date; continue; }
      payload[f.id] = v;
    }
    return { errors: errors, payload: payload };
  }

  // The visitor message that asks for a call back, so the assistant offers the form.
  var CALLBACK = {
    en: "I'd like a call back",
    hi: "मुझे कॉल बैक चाहिए",
    "hi-Latn": "Mujhe call back chahiye"
  };
  function callbackMessage(lang) {
    return CALLBACK[lang] || CALLBACK.en;
  }

  root.CobLeadForm = {
    validatePhone: validatePhone,
    validateEmail: validateEmail,
    collect: collect,
    messages: messages,
    callbackMessage: callbackMessage
  };
})(typeof window !== "undefined" ? window : globalThis);
