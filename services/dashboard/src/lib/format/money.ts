export type Currency = "INR" | "USD";

const MINUS = "−";
const SYMBOL: Record<Currency, string> = { INR: "₹", USD: "$" };
const MAX_INTEGER_DIGITS = 15;
const PARSE_RE = /^(-)?(\d+)(?:\.(\d{1,2}))?$/;

/** Thousands separators: Indian (3, then 2-2-2) for INR, western (3-3-3) for USD. */
function group(digits: string, currency: Currency): string {
	if (digits.length <= 3) return digits;
	if (currency === "INR") {
		let head = digits.slice(0, -3);
		const tail = digits.slice(-3);
		const parts: string[] = [];
		while (head.length > 2) {
			parts.unshift(head.slice(-2));
			head = head.slice(0, -2);
		}
		if (head) parts.unshift(head);
		return [...parts, tail].join(",");
	}
	return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Integer minor units (paise / cents) as money: `₹1,23,456.78`, `$123,456.78`, `−₹12.00`. Mirrors
 * `Money.format` in `billing/money.py`; both read `tests/fixtures/money_vectors.json`. Never goes
 * through a float, and never through the USD-based `currency.ts`.
 */
export function formatMinor(minor: number, currency: Currency): string {
	const abs = Math.abs(minor);
	const whole = Math.trunc(abs / 100);
	const frac = String(abs % 100).padStart(2, "0");
	return `${minor < 0 ? MINUS : ""}${SYMBOL[currency]}${group(String(whole), currency)}.${frac}`;
}

/** `formatMinor`, or an em dash for an amount the caller may not see (null/undefined). */
export function formatMoneyOrDash(minor: number | null | undefined, currency: Currency): string {
	return minor == null ? "—" : formatMinor(minor, currency);
}

/**
 * What a person typed as an amount → integer minor units, or null when it is not a valid amount.
 * `"1,234.5"` → 123450, `"₹ 10"` → 1000, `"-3"` → -300. Integer math on the string: at most two
 * decimals, no exponents, no `NaN`, at most 15 integer digits. Mirrors `Money.parse`.
 */
export function parseMajor(text: string, _currency?: Currency): number | null {
	const cleaned = text
		.trim()
		.replaceAll(MINUS, "-")
		.replaceAll("₹", "")
		.replaceAll("$", "")
		.replaceAll(",", "")
		.replaceAll(" ", "");
	const m = PARSE_RE.exec(cleaned);
	if (!m) return null;
	const whole = m[2] ?? "";
	if (whole.length > MAX_INTEGER_DIGITS) return null;
	const minor = Number(whole) * 100 + Number((m[3] ?? "").padEnd(2, "0"));
	if (!Number.isSafeInteger(minor)) return null;
	return m[1] ? -minor : minor;
}

/** Minor units as an editable amount: `123450` → `"1234.50"` (no symbol, no grouping). */
export function minorToInput(minor: number): string {
	const abs = Math.abs(minor);
	return `${minor < 0 ? "-" : ""}${Math.trunc(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** Basis points as a percentage: `2500` → `"25"`, `1250` → `"12.5"`. */
export function bpToPercent(bp: number): string {
	const whole = Math.trunc(bp / 100);
	const frac = String(bp % 100)
		.padStart(2, "0")
		.replace(/0+$/, "");
	return frac ? `${whole}.${frac}` : String(whole);
}

/** A percentage a person typed (`"25"`, `"12.5"`, `"12.50"`) → basis points, or null. */
export function parsePercent(text: string): number | null {
	const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(text.trim().replace(/%$/, "").trim());
	if (!m) return null;
	const bp = Number(m[1]) * 100 + Number((m[2] ?? "").padEnd(2, "0"));
	return Number.isSafeInteger(bp) ? bp : null;
}
