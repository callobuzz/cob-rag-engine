/** Citation markers: `[n]`, `[n, m]`, `[n-m]`, `【n】`, `^n`, `[^n]` (n up to 99). */

export interface CiteMatch {
	len: number;
	nums: number[];
}

const FULLWIDTH = /^【\s*(\d{1,2})\s*】/;
const FOOTNOTE = /^\[\^(\d{1,2})\]/;
const BRACKET = /^\[\s*\d{1,2}(?:\s*[,，–—-]\s*\d{1,2})*\s*\]/;
const CARET = /^\^(\d{1,2})(?!\d)/;

/** The citation marker that starts at `s[i]`, or null. A range spans at most ten numbers. */
export function matchCite(s: string, i: number): CiteMatch | null {
	const c = s[i];
	if (c !== "[" && c !== "【" && c !== "^") return null;
	const rest = s.slice(i, i + 40);
	let m: RegExpExecArray | null;
	if (c === "【") {
		m = FULLWIDTH.exec(rest);
		return m ? { len: m[0].length, nums: [Number(m[1])] } : null;
	}
	if (c === "^") {
		if (i > 0 && /\d/.test(s[i - 1] ?? "")) return null;
		m = CARET.exec(rest);
		return m ? { len: m[0].length, nums: [Number(m[1])] } : null;
	}
	m = FOOTNOTE.exec(rest);
	if (m) return { len: m[0].length, nums: [Number(m[1])] };
	m = BRACKET.exec(rest);
	if (!m) return null;
	const toks = m[0].match(/\d{1,2}|[,，–—-]/g) ?? [];
	const nums: number[] = [];
	for (let k = 0; k < toks.length; k += 2) {
		const n = Number(toks[k]);
		const sep = toks[k - 1];
		if (sep && sep !== "," && sep !== "，") {
			const from = nums[nums.length - 1] ?? 0;
			if (n <= from || n - from > 9) return null;
			for (let x = from + 1; x <= n; x++) nums.push(x);
		} else nums.push(n);
	}
	return { len: m[0].length, nums };
}

/** Rewrites 【n】, [^n] and a trailing-word `^n` as `[n]` (stored history, other callers). */
export function normalizeCitations(text: string): string {
	return text
		.replace(/【\s*(\d{1,2})\s*】/g, "[$1]")
		.replace(/\[\^(\d{1,2})\]/g, "[$1]")
		.replace(/(?<=[\p{L}.,;:!?)”"'])\^(\d{1,2})(?![\p{L}\p{N}])/gu, "[$1]");
}
