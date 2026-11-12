// TV2 reference: a reference code like SD-4F7K, typed the way people type it.

/** The engine's alphabet: no 0 O 1 I L U, so a code read out on a call is never mistaken. */
const ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ";
const SHAPE = new RegExp(`^([A-Z]{2})([${ALPHABET}]{4})$`);

/** `sd4f7k`, `SD 4F7K`, `sd-4f7k.` -> `SD-4F7K`; anything else (or a look-alike letter) -> null. */
export function normalizeReference(raw: string): string | null {
	const compact = raw.replace(/[\s.-]/g, "").toUpperCase();
	const m = SHAPE.exec(compact);
	return m ? `${m[1]}-${m[2]}` : null;
}
