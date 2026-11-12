/*! cob-rag-engine widget Markdown (GFM subset, no raw HTML, no dependencies) - (c) 2026 Callobuzz | SPDX-License-Identifier: MIT.
 * window.CobMd = { render, renderStream, inline, safeUrl, stream }.
 * Same rules as the dashboard's lib/markdown; tests/fixtures/markdown/conformance.json holds both.
 * Model text is only ever escaped (render) or set as text nodes (stream); no HTML parsing of model text anywhere. */
(function (g) {
  "use strict";
  var VOID = { br: 1, hr: 1 };
  var NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", colon: ":", tab: "\t", newline: "\n", lpar: "(", rpar: ")", sol: "/" };
  var STRIP = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g;
  var ESCAPABLE = "\\`*_{}[]()#+-.!|~<>^&\"'$%:;=?@/,";
  var FENCE = /^([ \t]*)(`{3,}|~{3,})(.*)$/;
  var HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
  var HR = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
  var QUOTE = /^ {0,3}> ?/;
  var ITEM = /^([ \t]*)([-*+\u2022]|\d{1,9}[.)])[ \t]+(\S.*)$/;
  var DELIM = /^\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?$/;
  var LIST_LINE = /^[ \t]*(?:[-*+\u2022]|\d{1,9}[.)])[ \t]+\S/;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function clean(t) { return String(t).replace(/\r\n?/g, "\n").replace(STRIP, ""); }
  function cp2s(cp) {
    if (cp < 0x10000) return String.fromCharCode(cp);
    cp -= 0x10000;
    return String.fromCharCode(0xd800 + (cp >> 10), 0xdc00 + (cp & 1023));
  }
  function decodeOnce(s) {
    return s.replace(/&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|([a-zA-Z]+));?/g, function (whole, dec, hex, name) {
      if (name) { var v = NAMED[name.toLowerCase()]; return v === undefined ? whole : v; }
      var cp = dec ? Number(dec) : parseInt(hex, 16);
      return cp > 0 && cp <= 0x10ffff ? cp2s(cp) : whole;
    });
  }
  function safeUrl(url) {
    var st = function (s) { return s.replace(/[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200d\u2060\ufeff]/g, ""); };
    var raw = st(String(url).replace(/^\s+|\s+$/g, "")).replace(/^\s+|\s+$/g, "");
    // The decoded form is only for checking (what a browser might see); the raw form is returned.
    var dec = st(decodeOnce(raw)).replace(/^\s+|\s+$/g, "");
    return allowed(raw) && allowed(dec) ? raw : null;
  }
  function allowed(s) {
    if (!s || s.length > 2048) return false;
    var m = /^([a-z][a-z0-9+.-]*):/i.exec(s);
    if (!m) return false;
    var sc = m[1].toLowerCase();
    if (sc !== "http" && sc !== "https" && sc !== "mailto" && sc !== "tel") return false;
    var rest = s.slice(m[0].length);
    if (sc === "http" || sc === "https") return /^\/\/[^\s\/?#\\]+/.test(rest);
    return !!rest;
  }
  function isWord(c) { return c !== undefined && /[A-Za-z0-9\u00C0-\u1FFF\u2C00-\u2FFF\u3040-\uFFEF]/.test(c); }
  function el(t, a, c) { return { t: t, a: a || [], c: c || [] }; }

  // ---- citations
  function matchCite(s, i) {
    var c = s[i];
    if (c !== "[" && c !== "\u3010" && c !== "^") return null;
    var rest = s.slice(i, i + 40), m;
    if (c === "\u3010") { m = /^\u3010\s*(\d{1,2})\s*\u3011/.exec(rest); return m ? { len: m[0].length, nums: [Number(m[1])] } : null; }
    if (c === "^") { if (i > 0 && /\d/.test(s[i - 1])) return null; m = /^\^(\d{1,2})(?!\d)/.exec(rest); return m ? { len: m[0].length, nums: [Number(m[1])] } : null; }
    m = /^\[\^(\d{1,2})\]/.exec(rest);
    if (m) return { len: m[0].length, nums: [Number(m[1])] };
    m = /^\[\s*\d{1,2}(?:\s*[,\uff0c\u2013\u2014-]\s*\d{1,2})*\s*\]/.exec(rest);
    if (!m) return null;
    var toks = m[0].match(/\d{1,2}|[,\uff0c\u2013\u2014-]/g) || [], nums = [];
    for (var k = 0; k < toks.length; k += 2) {
      var n = Number(toks[k]), sep = toks[k - 1];
      if (sep && sep !== "," && sep !== "\uff0c") {
        var from = nums[nums.length - 1];
        if (n <= from || n - from > 9) return null;
        for (var x = from + 1; x <= n; x++) nums.push(x);
      } else nums.push(n);
    }
    return { len: m[0].length, nums: nums };
  }

  // ---- inline
  function findRun(s, from, n) {
    for (var j = from; j < s.length;) {
      if (s[j] !== "`") { j++; continue; }
      var m = 1;
      while (s[j + m] === "`") m++;
      if (m === n) return j;
      j += m;
    }
    return -1;
  }
  function findCloser(s, from, ch, k) {
    for (var j = from; j < s.length;) {
      var c = s[j];
      if (c === "\\") { j += 2; continue; }
      if (c === "`") {
        var m0 = 1;
        while (s[j + m0] === "`") m0++;
        var e = findRun(s, j + m0, m0);
        j = e >= 0 ? e + m0 : j + m0;
        continue;
      }
      if (c === ch) {
        var m = 1;
        while (s[j + m] === ch) m++;
        var prev = s[j - 1];
        if (m === k && j > from && prev !== undefined && !/\s/.test(prev) && !(ch === "_" && isWord(s[j + k]))) return j;
        j += m;
        continue;
      }
      j++;
    }
    return -1;
  }
  function matchLink(s, i) {
    var depth = 1, j = i + 1;
    for (; j < s.length && depth > 0; j++) {
      if (s[j] === "\\") j++;
      else if (s[j] === "[") depth++;
      else if (s[j] === "]") depth--;
    }
    if (depth > 0 || s[j] !== "(") return null;
    var text = s.slice(i + 1, j - 1), p = j + 1, url;
    while (s[p] === " " || s[p] === "\t") p++;
    if (s[p] === "<") {
      var e = s.indexOf(">", p);
      if (e < 0) return null;
      url = s.slice(p + 1, e);
      p = e + 1;
    } else {
      var start = p, d = 0;
      for (; p < s.length; p++) {
        var c = s[p];
        if (/\s/.test(c)) break;
        if (c === "(") d++;
        else if (c === ")") { if (d === 0) break; d--; }
      }
      url = s.slice(start, p);
    }
    while (p < s.length && /\s/.test(s[p])) p++;
    if (s[p] === '"' || s[p] === "'") {
      var q = s.indexOf(s[p], p + 1);
      if (q < 0) return null;
      p = q + 1;
      while (p < s.length && /\s/.test(s[p])) p++;
    }
    if (s[p] !== ")") return null;
    return { text: text, url: url, end: p + 1 };
  }
  function count(s, c) { return s.split(c).length; }
  function trimUrl(u) {
    for (;;) {
      var last = u.charAt(u.length - 1);
      if (last && ".,;:!?'\"*~".indexOf(last) >= 0) u = u.slice(0, -1);
      else if (last === ")" && count(u, ")") > count(u, "(")) u = u.slice(0, -1);
      else if (last === "]" && count(u, "]") > count(u, "[")) u = u.slice(0, -1);
      else return u;
    }
  }
  function rep(c, n) { var r = ""; while (n-- > 0) r += c; return r; }

  // o: { src: {n: true} | null, open: bool }. Returns an array of strings and element nodes.
  function run(s, o, depth, inLink) {
    var out = [], buf = "", failed = {}, i = 0;
    function flush() { if (buf) { out.push(buf); buf = ""; } }
    while (i < s.length) {
      var ch = s[i], n, m, k;
      if (ch === "\\") {
        var nx = s[i + 1];
        if (nx === "\n") { flush(); out.push(el("br")); i += 2; }
        else if (nx !== undefined && ESCAPABLE.indexOf(nx) >= 0) { buf += nx; i += 2; }
        else { buf += ch; i++; }
        continue;
      }
      if (ch === "\n") { buf = buf.replace(/ +$/, ""); flush(); out.push(el("br")); i++; continue; }
      if (ch === "`") {
        n = 1;
        while (s[i + n] === "`") n++;
        var close = findRun(s, i + n, n);
        if (close >= 0 || (o.open && i + n < s.length)) {
          var code = s.slice(i + n, close >= 0 ? close : s.length).replace(/\n/g, " ");
          if (code.length > 2 && code.charAt(0) === " " && code.charAt(code.length - 1) === " " && code.replace(/^\s+|\s+$/g, "")) code = code.slice(1, -1);
          flush();
          out.push(el("code", [], [code]));
          i = close >= 0 ? close + n : s.length;
          continue;
        }
        buf += rep("`", n); i += n;
        continue;
      }
      if (ch === "[" || ch === "\u3010" || ch === "^") {
        var cite = inLink ? null : matchCite(s, i);
        if (cite && !(ch === "[" && s[i + cite.len] === "(")) {
          var ok = !o.src;
          if (!ok) { ok = true; for (k = 0; k < cite.nums.length; k++) if (!o.src[cite.nums[k]]) ok = false; }
          if (ok) {
            flush();
            for (k = 0; k < cite.nums.length; k++) out.push(el("button", ["type", "button", "class", "md-cite", "data-cite", String(cite.nums[k])], [String(cite.nums[k])]));
          } else buf += s.slice(i, i + cite.len);
          i += cite.len;
          continue;
        }
      }
      if (ch === "[" || (ch === "!" && s[i + 1] === "[")) {
        var lm = !inLink && depth < 6 ? matchLink(s, ch === "!" ? i + 1 : i) : null;
        if (lm) {
          var kids = run(lm.text, { src: o.src, open: false }, depth + 1, true), href = safeUrl(lm.url);
          flush();
          if (href) out.push(el("a", ["href", href, "target", "_blank", "rel", "noopener noreferrer nofollow"], kids.length ? kids : [lm.url]));
          else for (k = 0; k < kids.length; k++) out.push(kids[k]);
          i = lm.end;
          continue;
        }
        buf += ch; i++;
        continue;
      }
      if ((ch === "h" || ch === "H") && !inLink && !/[\w@\/]/.test(i > 0 ? s[i - 1] : " ")) {
        m = /^https?:\/\/[^\s<>]+/i.exec(s.slice(i));
        if (m) {
          var url = trimUrl(m[0]), h2 = safeUrl(url);
          if (h2) {
            flush();
            out.push(el("a", ["href", h2, "target", "_blank", "rel", "noopener noreferrer nofollow"], [url]));
            i += url.length;
            continue;
          }
        }
      }
      if (ch === "*" || ch === "_" || ch === "~") {
        n = 1;
        while (s[i + n] === ch) n++;
        var next = s[i + n];
        if ((ch === "~" ? n === 2 : n <= 3) && depth < 6 && next !== undefined && !/\s/.test(next) && !(ch === "_" && isWord(s[i - 1]))) {
          var key = ch + n, cl = failed[key] ? -1 : findCloser(s, i + n, ch, n);
          if (cl < 0) failed[key] = true;
          if (cl >= 0 || o.open) {
            var inner = run(s.slice(i + n, cl >= 0 ? cl : s.length), { src: o.src, open: cl < 0 }, depth + 1, inLink);
            if (n === 3) inner = [el("em", [], inner)];
            flush();
            out.push(el(ch === "~" ? "del" : n === 1 ? "em" : "strong", [], inner));
            i = cl >= 0 ? cl + n : s.length;
            continue;
          }
        }
        buf += rep(ch, n); i += n;
        continue;
      }
      buf += ch; i++;
    }
    flush();
    return out;
  }

  // ---- blocks (raw: inline content stays a source string until toNodes)
  function indentOf(line) {
    var n = 0;
    for (var i = 0; i < line.length; i++) {
      var c = line.charAt(i);
      if (c === " ") n++; else if (c === "\t") n += 4; else break;
    }
    return n;
  }
  function fenceOpen(line) {
    var m = FENCE.exec(line);
    if (!m) return null;
    var info = m[3].replace(/^\s+|\s+$/g, "");
    if (m[2].charAt(0) === "`" && info.indexOf("`") >= 0) return null;
    var word = info.split(/\s+/)[0] || "";
    return { indent: m[1].length, ch: m[2].charAt(0), len: m[2].length, lang: /^[A-Za-z0-9_+#.-]{1,24}$/.test(word) ? word : "" };
  }
  function fenceCloses(line, ch, len) {
    var m = /^[ \t]*(`{3,}|~{3,})[ \t]*$/.exec(line);
    return !!m && m[1].charAt(0) === ch && m[1].length >= len;
  }
  function splitRow(line) {
    var s = line.replace(/^\s+|\s+$/g, ""), cells = [], cur = "", ended = false;
    for (var i = s.charAt(0) === "|" ? 1 : 0; i < s.length; i++) {
      ended = false;
      if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; }
      else if (s[i] === "|") { cells.push(cur.replace(/^\s+|\s+$/g, "")); cur = ""; ended = true; }
      else cur += s[i];
    }
    if (!ended) cells.push(cur.replace(/^\s+|\s+$/g, ""));
    return cells;
  }
  function isTableStart(lines, i) {
    var head = lines[i], delim = lines[i + 1];
    if (head === undefined || delim === undefined || head.indexOf("|") < 0 || delim.indexOf("|") < 0) return false;
    if (!DELIM.test(delim.replace(/^\s+|\s+$/g, ""))) return false;
    return splitRow(head).length === splitRow(delim).length;
  }
  function isBlockStart(lines, i) {
    var l = lines[i];
    return fenceOpen(l) !== null || HEADING.test(l) || HR.test(l) || QUOTE.test(l) || isTableStart(lines, i);
  }
  function startsList(line, interrupting) {
    if (HR.test(line)) return false;
    var m = ITEM.exec(line);
    if (!m) return false;
    return !interrupting || !/\d/.test(m[2]) || parseInt(m[2], 10) === 1;
  }
  function blank(l) { return l.replace(/^\s+|\s+$/g, "") === ""; }

  function parseList(lines, start) {
    var first = ITEM.exec(lines[start]), base = indentOf(first[1]), ordered = /\d/.test(first[2]);
    var items = [], sub = null, cur = null, i = start;
    while (i < lines.length) {
      var line = lines[i];
      if (blank(line)) {
        var k = i;
        while (k < lines.length && blank(lines[k])) k++;
        if (k >= lines.length) break;
        if (!(ITEM.exec(lines[k]) || (indentOf(lines[k]) >= base + 2 && !isBlockStart(lines, k)))) break;
        i = k;
        continue;
      }
      var m = HR.test(line) ? null : ITEM.exec(line);
      if (m) {
        var isOrd = /\d/.test(m[2]);
        if (indentOf(m[1]) >= base + 2 && cur) {
          var item = { c: m[3], sub: null };
          if (!sub) {
            sub = { t: "list", ordered: isOrd, start: isOrd ? parseInt(m[2], 10) : 1, items: [] };
            items[items.length - 1].sub = sub;
          }
          sub.items.push(item);
          cur = item;
        } else {
          if (isOrd !== ordered) break;
          sub = null;
          cur = { c: m[3], sub: null };
          items.push(cur);
        }
        i++;
        continue;
      }
      if (isBlockStart(lines, i)) break;
      if (cur) cur.c += "\n" + line.replace(/^\s+|\s+$/g, "");
      i++;
    }
    return { block: { t: "list", ordered: ordered, start: ordered ? parseInt(first[2], 10) : 1, items: items }, next: i };
  }

  function parseBlocks(lines, depth) {
    var out = [], i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (blank(line)) { i++; continue; }
      var fence = fenceOpen(line);
      if (fence) {
        var body = [], strip = new RegExp("^[ \\t]{0," + fence.indent + "}");
        i++;
        while (i < lines.length && !fenceCloses(lines[i], fence.ch, fence.len)) { body.push(lines[i].replace(strip, "")); i++; }
        i++;
        out.push({ t: "code", lang: fence.lang, v: body.join("\n") });
        continue;
      }
      var h = HEADING.exec(line);
      if (h) { out.push({ t: "h", level: Math.min(h[1].length, 4), c: h[2] }); i++; continue; }
      if (HR.test(line)) { out.push({ t: "hr" }); i++; continue; }
      if (QUOTE.test(line) && depth < 3) {
        var inner = [];
        while (i < lines.length && QUOTE.test(lines[i])) { inner.push(lines[i].replace(QUOTE, "")); i++; }
        out.push({ t: "quote", c: parseBlocks(inner, depth + 1) });
        continue;
      }
      if (startsList(line, false)) { var r = parseList(lines, i); out.push(r.block); i = r.next; continue; }
      if (isTableStart(lines, i)) {
        var align = [], dl = splitRow(lines[i + 1]), w = dl.length, c, rows = [];
        for (c = 0; c < w; c++) {
          var l = dl[c].charAt(0) === ":", rt = dl[c].slice(-1) === ":";
          align.push(l && rt ? "center" : rt ? "right" : l ? "left" : null);
        }
        var fit = function (cells) { var o = []; for (var q = 0; q < w; q++) o.push(cells[q] === undefined ? "" : cells[q]); return o; };
        var head = fit(splitRow(line));
        i += 2;
        while (i < lines.length && !blank(lines[i]) && lines[i].indexOf("|") >= 0 && fenceOpen(lines[i]) === null) { rows.push(fit(splitRow(lines[i]))); i++; }
        out.push({ t: "table", align: align, head: head, rows: rows });
        continue;
      }
      var para = [line.replace(/^\s+|\s+$/g, "")];
      i++;
      while (i < lines.length && !blank(lines[i]) && !isBlockStart(lines, i) && !startsList(lines[i], true)) { para.push(lines[i].replace(/^\s+|\s+$/g, "")); i++; }
      out.push({ t: "p", c: para.join("\n") });
    }
    return out;
  }

  function trimPartialCite(t) {
    return t.replace(/(?:\[\^?\d{0,2}(?:[ \t]*[,\uff0c\u2013\u2014-][ \t]*\d{0,2})*[ \t]*|\u3010[ \t]*\d{0,2}[ \t]*|\^)$/, "");
  }
  // A line that may still become a table row: edge pipes, or short cells with no sentence in them.
  function headerLike(line) {
    var t = line.replace(/^\s+|\s+$/g, "");
    if (t.indexOf("|") < 0) return false;
    if (t.charAt(0) === "|" || t.charAt(t.length - 1) === "|") return true;
    if (t.length > 80) return false;
    var cells = t.split("|");
    for (var i = 0; i < cells.length; i++) {
      if (/[.…!?,;:]/.test(cells[i]) || cells[i].replace(/^\s+|\s+$/g, "").split(/\s+/).length > 2) return false;
    }
    return true;
  }
  function holdBack(blocks, text) {
    var last = blocks[blocks.length - 1];
    if (!last) return blocks;
    if (last.t === "table") {
      if (text.slice(-1) !== "\n" && last.rows.length > 0) last.rows.pop();
    } else if (last.t === "p") {
      var lines = last.c.split("\n"), hold = 0;
      while (hold < 2 && hold < lines.length && headerLike(lines[lines.length - 1 - hold])) hold++;
      if (hold >= lines.length) blocks.pop();
      else if (hold > 0) last.c = lines.slice(0, lines.length - hold).join("\n");
    }
    return blocks;
  }

  function toNodes(b, inl) {
    var i, kids;
    switch (b.t) {
      case "p": return el("p", [], inl(b.c));
      case "h": return el("h" + (b.level + 2), [], inl(b.c));
      case "code": return el("pre", [], [el("code", b.lang ? ["class", "lang-" + b.lang] : [], [b.v])]);
      case "hr": return el("hr");
      case "quote":
        kids = [];
        for (i = 0; i < b.c.length; i++) kids.push(toNodes(b.c[i], inl));
        return el("blockquote", [], kids);
      case "list":
        kids = [];
        for (i = 0; i < b.items.length; i++) {
          var it = b.items[i], c = inl(it.c);
          if (it.sub) c.push(toNodes(it.sub, inl));
          kids.push(el("li", [], c));
        }
        return el(b.ordered ? "ol" : "ul", b.ordered && b.start !== 1 ? ["start", String(b.start)] : [], kids);
      default:
        var cls = function (a) { return a ? ["class", "md-" + a] : []; }, head = [], body = [], r, k;
        for (k = 0; k < b.head.length; k++) head.push(el("th", ["scope", "col"].concat(cls(b.align[k])), inl(b.head[k])));
        for (r = 0; r < b.rows.length; r++) {
          var tds = [];
          for (k = 0; k < b.rows[r].length; k++) tds.push(el("td", cls(b.align[k]), inl(b.rows[r][k])));
          body.push(el("tr", [], tds));
        }
        var tbl = [el("thead", [], [el("tr", [], head)])];
        if (body.length) tbl.push(el("tbody", [], body));
        return el("div", ["class", "md-table", "role", "region", "tabindex", "0", "aria-label", "Table"], [el("table", [], tbl)]);
    }
  }

  function srcSet(opts) {
    if (!opts || !opts.sources) return null;
    var o = {};
    for (var i = 0; i < opts.sources.length; i++) o[opts.sources[i]] = true;
    return o;
  }
  // The blocks of one text; `streaming` marks its last block provisional.
  function blocksOf(text, src, streaming) {
    var s = clean(text);
    if (streaming) s = trimPartialCite(s);
    var raw = parseBlocks(s.split("\n"), 0);
    if (streaming) raw = holdBack(raw, s);
    var out = [];
    for (var i = 0; i < raw.length; i++) {
      var open = streaming && i === raw.length - 1;
      out.push(toNodes(raw[i], function (t) { return run(t, { src: src, open: open }, 0, false); }));
    }
    return out;
  }

  // ---- streaming: stable chunks are never re-rendered
  function boundaries(text) {
    var lines = text.split("\n"), out = [], offset = 0, fence = null, seen = false, pending = -1;
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      offset += line.length + 1;
      if (fence) {
        var m = /^[ \t]*(`{3,}|~{3,})[ \t]*$/.exec(line);
        if (m && m[1].charAt(0) === fence.ch && m[1].length >= fence.len) fence = null;
        continue;
      }
      if (blank(line)) { if (seen && pending < 0) pending = offset; continue; }
      if (pending >= 0) {
        if (i < lines.length - 1 && !LIST_LINE.test(line) && indentOf(line) < 2) out.push(pending);
        pending = -1;
      }
      seen = true;
      var f = fenceOpen(line);
      if (f) fence = { ch: f.ch, len: f.len };
    }
    return out;
  }
  function chunks(text) {
    var s = clean(text), b = boundaries(s), stable = [], from = 0;
    for (var i = 0; i < b.length; i++) { stable.push(s.slice(from, b[i])); from = b[i]; }
    return { stable: stable, tail: s.slice(from) };
  }

  // ---- output
  function toHtml(n) {
    if (typeof n === "string") return esc(n);
    var a = "";
    for (var i = 0; i < n.a.length; i += 2) a += " " + n.a[i] + '="' + esc(n.a[i + 1]) + '"';
    if (VOID[n.t]) return "<" + n.t + a + ">";
    var c = "";
    for (var j = 0; j < n.c.length; j++) c += toHtml(n.c[j]);
    return "<" + n.t + a + ">" + c + "</" + n.t + ">";
  }
  function allHtml(list) { var h = ""; for (var i = 0; i < list.length; i++) h += toHtml(list[i]); return h; }
  function toDom(n, doc) {
    if (typeof n === "string") return doc.createTextNode(n);
    var e = doc.createElement(n.t), i;
    for (i = 0; i < n.a.length; i += 2) e.setAttribute(n.a[i], n.a[i + 1]);
    for (i = 0; i < n.c.length; i++) e.appendChild(toDom(n.c[i], doc));
    return e;
  }

  function nodesOf(text, opts) {
    var src = srcSet(opts), streaming = !!(opts && opts.streaming), c = chunks(text), out = [];
    for (var i = 0; i < c.stable.length; i++) out = out.concat(blocksOf(c.stable[i], src, false));
    return out.concat(blocksOf(c.tail, src, streaming));
  }

  var CobMd = {
    safeUrl: safeUrl,
    /** Block Markdown to an HTML string (every text escaped; only our own tags). */
    render: function (text, opts) { return allHtml(nodesOf(text, opts)); },
    /** One line of Markdown, no block syntax. */
    inline: function (text, opts) {
      return allHtml(run(clean(text).replace(/^\s+|\s+$/g, ""), { src: srcSet(opts), open: !!(opts && opts.streaming) }, 0, false));
    },
    /** html of the whole text; `state` carries the stable chunks' html so they are not rebuilt. */
    renderStream: function (state, text, opts) {
      var c = chunks(text), src = srcSet(opts), st = state && state.n <= c.stable.length && clean(text).indexOf(state.pre) === 0 ? state : { n: 0, pre: "", html: "" };
      var html = st.html, pre = st.pre;
      for (var i = st.n; i < c.stable.length; i++) { html += allHtml(blocksOf(c.stable[i], src, false)); pre += c.stable[i]; }
      return { html: html + allHtml(blocksOf(c.tail, src, !(opts && opts.final))), state: { n: c.stable.length, pre: pre, html: html } };
    },
    /** Incremental DOM: `.md-stable` only ever appends, `.md-tail` is replaced. update(text, final). */
    stream: function (host, opts) {
      var doc = host.ownerDocument, stable = doc.createElement("div"), tail = doc.createElement("div"), done = 0, pre = "";
      stable.className = "md-stable";
      tail.className = "md-tail";
      host.appendChild(stable);
      host.appendChild(tail);
      return {
        update: function (text, final) {
          var c = chunks(text), src = srcSet(opts), i, j, nodes;
          if (clean(text).indexOf(pre) !== 0) { while (stable.firstChild) stable.removeChild(stable.firstChild); done = 0; pre = ""; }
          for (i = done; i < c.stable.length; i++) {
            nodes = blocksOf(c.stable[i], src, false);
            for (j = 0; j < nodes.length; j++) stable.appendChild(toDom(nodes[j], doc));
            pre += c.stable[i];
          }
          done = c.stable.length;
          while (tail.firstChild) tail.removeChild(tail.firstChild);
          nodes = blocksOf(c.tail, src, !final);
          for (j = 0; j < nodes.length; j++) tail.appendChild(toDom(nodes[j], doc));
        },
      };
    },
    _chunks: chunks,
  };
  g.CobMd = CobMd;
})(typeof window !== "undefined" ? window : this);
