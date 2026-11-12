/** "+919876543210" as "+91 98765 43210"; other numbers are shown as the engine stored them. */
export function formatPhone(e164: string | null | undefined): string {
	if (!e164) return "";
	const m = /^\+91(\d{5})(\d{5})$/.exec(e164);
	return m ? `+91 ${m[1]} ${m[2]}` : e164;
}

/** The name, else the number, else the e-mail: what a person would call this lead. */
export function leadLabel(contact: { name?: string | null; phone?: string | null; email?: string | null }): string {
	return contact.name || formatPhone(contact.phone) || contact.email || "Lead";
}
