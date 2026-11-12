import { matchCite } from "./cite";
import type { MdInline } from "./types";
import { internalUrl, safeUrl } from "./url";

export interface InlineOptions {
	/** Numbers a citation may point at; null/undefined accepts all. */
	sources?: ReadonlySet<number> | null;
	/** The text runs to the end of what has arrived: unclosed markers close there. */
	open?: boolean;
	/** Docs pages: keep links to a path on this site (`/docs/...`) and to an anchor (`#x`). */
	internal?: boolean;
}

const ESCAPABLE = "\\`*_{}[]()#+-.!|~<>^&\"'$%:;=?@/,";
const MAX_DEPTH = 6;

/** Letters and digits of any script (Devanagari, Tamil...), not general punctuation. */
export function isWord(c: string | undefined): boolean {
	return c !== undefined && /[A-Za-z0-9\u00C0-\u1FFF\u2C00-\u2FFF\u3040-\uFFEF]/.test(c);
}

function findRun(s: string, from: number, n: number): number {
	for (let j = from; j < s.length; ) {
		if (s[j] !== "`") {
			j++;
			continue;
		}
		let m = 1;
		while (s[j + m] === "`") m++;
		if (m === n) return j;
		j += m;
	}
	return -1;
}

function findCloser(s: string, from: number, ch: string, k: number): number {
	for (let j = from; j < s.length; ) {
		const c = s[j];
		if (c === "\\") {
			j += 2;
			continue;
		}
		if (c === "`") {
			let m = 1;
			while (s[j + m] === "`") m++;
			const e = findRun(s, j + m, m);
			j = e >= 0 ? e + m : j + m;
			continue;
		}
		if (c === ch) {
			let m = 1;
			while (s[j + m] === ch) m++;
			const prev = s[j - 1];
			if (m === k && j > from && prev !== undefined && !/\s/.test(prev) && !(ch === "_" && isWord(s[j + k]))) {
				return j;
			}
			j += m;
			continue;
		}
		j++;
	}
	return -1;
}

interface LinkMatch {
	text: string;
	url: string;
	end: number;
}

/** `[text](url "title")` starting at the `[` at `i`. */
function matchLink(s: string, i: number): LinkMatch | null {
	let depth = 1;
	let j = i + 1;
	for (; j < s.length && depth > 0; j++) {
		if (s[j] === "\\") j++;
		else if (s[j] === "[") depth++;
		else if (s[j] === "]") depth--;
	}
	if (depth > 0 || s[j] !== "(") return null;
	const text = s.slice(i + 1, j - 1);
	let p = j + 1;
	while (s[p] === " " || s[p] === "\t") p++;
	let url: string;
	if (s[p] === "<") {
		const e = s.indexOf(">", p);
		if (e < 0) return null;
		url = s.slice(p + 1, e);
		p = e + 1;
	} else {
		const start = p;
		let d = 0;
		for (; p < s.length; p++) {
			const c = s.charAt(p);
			if (/\s/.test(c)) break;
			if (c === "(") d++;
			else if (c === ")") {
				if (d === 0) break;
				d--;
			}
		}
		url = s.slice(start, p);
	}
	while (p < s.length && /\s/.test(s.charAt(p))) p++;
	if (s[p] === '"' || s[p] === "'") {
		const e = s.indexOf(s.charAt(p), p + 1);
		if (e < 0) return null;
		p = e + 1;
		while (p < s.length && /\s/.test(s.charAt(p))) p++;
	}
	if (s[p] !== ")") return null;
	return { text, url, end: p + 1 };
}

const BARE = /https?:\/\/[^\s<>]+/iy;

function trimUrl(u: string): string {
	for (;;) {
		const last = u[u.length - 1];
		if (last && ".,;:!?'\"*~".includes(last)) u = u.slice(0, -1);
		else if (last === ")" && u.split(")").length > u.split("(").length) u = u.slice(0, -1);
		else if (last === "]" && u.split("]").length > u.split("[").length) u = u.slice(0, -1);
		else return u;
	}
}

function run(s: string, o: InlineOptions, depth: number, inLink: boolean): MdInline[] {
	const out: MdInline[] = [];
	let buf = "";
	const flush = () => {
		if (buf) {
			out.push({ t: "text", v: buf });
			buf = "";
		}
	};
	const failed: Record<string, boolean> = {};
	let i = 0;
	while (i < s.length) {
		const ch = s[i];
		if (ch === "\\") {
			const nx = s[i + 1];
			if (nx === "\n") {
				flush();
				out.push({ t: "br" });
				i += 2;
			} else if (nx !== undefined && ESCAPABLE.includes(nx)) {
				buf += nx;
				i += 2;
			} else {
				buf += ch;
				i++;
			}
			continue;
		}
		if (ch === "\n") {
			buf = buf.replace(/ +$/, "");
			flush();
			out.push({ t: "br" });
			i++;
			continue;
		}
		if (ch === "`") {
			let n = 1;
			while (s[i + n] === "`") n++;
			const close = findRun(s, i + n, n);
			if (close >= 0 || (o.open && i + n < s.length)) {
				const end = close >= 0 ? close : s.length;
				let code = s.slice(i + n, end).replace(/\n/g, " ");
				if (code.length > 2 && code.startsWith(" ") && code.endsWith(" ") && code.trim()) code = code.slice(1, -1);
				flush();
				out.push({ t: "code", v: code });
				i = close >= 0 ? close + n : s.length;
				continue;
			}
			buf += "`".repeat(n);
			i += n;
			continue;
		}
		if (ch === "[" || ch === "【" || ch === "^") {
			const cite = inLink ? null : matchCite(s, i);
			if (cite && !(ch === "[" && s[i + cite.len] === "(")) {
				if (!o.sources || cite.nums.every((n) => o.sources?.has(n))) {
					flush();
					for (const n of cite.nums) out.push({ t: "cite", n });
				} else buf += s.slice(i, i + cite.len);
				i += cite.len;
				continue;
			}
		}
		if (ch === "[" || (ch === "!" && s[i + 1] === "[")) {
			const at = ch === "!" ? i + 1 : i;
			const lm = !inLink && depth < MAX_DEPTH ? matchLink(s, at) : null;
			if (lm) {
				const kids = run(lm.text, { ...o, open: false }, depth + 1, true);
				const href = safeUrl(lm.url) ?? (o.internal ? internalUrl(lm.url) : null);
				flush();
				if (href) out.push({ t: "link", href, c: kids.length ? kids : [{ t: "text", v: lm.url }] });
				else out.push(...kids);
				i = lm.end;
				continue;
			}
			buf += ch;
			i++;
			continue;
		}
		if ((ch === "h" || ch === "H") && !inLink && !/[\w@/]/.test(s[i - 1] ?? " ")) {
			BARE.lastIndex = i;
			const m = BARE.exec(s);
			if (m) {
				const url = trimUrl(m[0]);
				const href = safeUrl(url);
				if (href) {
					flush();
					out.push({ t: "link", href, c: [{ t: "text", v: url }] });
					i += url.length;
					continue;
				}
			}
		}
		if (ch === "*" || ch === "_" || ch === "~") {
			let n = 1;
			while (s[i + n] === ch) n++;
			const next = s[i + n];
			const opens =
				(ch === "~" ? n === 2 : n <= 3) &&
				depth < MAX_DEPTH &&
				next !== undefined &&
				!/\s/.test(next) &&
				!(ch === "_" && isWord(s[i - 1]));
			if (opens) {
				const key = ch + n;
				const close = failed[key] ? -1 : findCloser(s, i + n, ch, n);
				if (close < 0) failed[key] = true;
				if (close >= 0 || o.open) {
					const end = close >= 0 ? close : s.length;
					let kids = run(s.slice(i + n, end), { ...o, open: close < 0 }, depth + 1, inLink);
					if (n === 3) kids = [{ t: "em", c: kids }];
					flush();
					out.push({ t: ch === "~" ? "del" : n === 1 ? "em" : "strong", c: kids });
					i = close >= 0 ? close + n : s.length;
					continue;
				}
			}
			buf += ch.repeat(n);
			i += n;
			continue;
		}
		buf += ch;
		i++;
	}
	flush();
	return merge(out);
}

function merge(nodes: MdInline[]): MdInline[] {
	const out: MdInline[] = [];
	for (const n of nodes) {
		const last = out[out.length - 1];
		if (n.t === "text" && last?.t === "text") last.v += n.v;
		else out.push(n);
	}
	return out;
}

/** Inline Markdown to nodes. Never throws; text that is not a recognised form stays text. */
export function parseInline(src: string, o: InlineOptions = {}): MdInline[] {
	return run(src, o, 0, false);
}
