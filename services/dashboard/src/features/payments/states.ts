/** The GST state codes, as on the GST portal (mirrors `core/company.py:STATES` in the engine). */
export const GST_STATES: ReadonlyArray<{ code: string; name: string }> = [
	{ code: "01", name: "Jammu and Kashmir" },
	{ code: "02", name: "Himachal Pradesh" },
	{ code: "03", name: "Punjab" },
	{ code: "04", name: "Chandigarh" },
	{ code: "05", name: "Uttarakhand" },
	{ code: "06", name: "Haryana" },
	{ code: "07", name: "Delhi" },
	{ code: "08", name: "Rajasthan" },
	{ code: "09", name: "Uttar Pradesh" },
	{ code: "10", name: "Bihar" },
	{ code: "11", name: "Sikkim" },
	{ code: "12", name: "Arunachal Pradesh" },
	{ code: "13", name: "Nagaland" },
	{ code: "14", name: "Manipur" },
	{ code: "15", name: "Mizoram" },
	{ code: "16", name: "Tripura" },
	{ code: "17", name: "Meghalaya" },
	{ code: "18", name: "Assam" },
	{ code: "19", name: "West Bengal" },
	{ code: "20", name: "Jharkhand" },
	{ code: "21", name: "Odisha" },
	{ code: "22", name: "Chhattisgarh" },
	{ code: "23", name: "Madhya Pradesh" },
	{ code: "24", name: "Gujarat" },
	{ code: "26", name: "Dadra and Nagar Haveli and Daman and Diu" },
	{ code: "27", name: "Maharashtra" },
	{ code: "29", name: "Karnataka" },
	{ code: "30", name: "Goa" },
	{ code: "31", name: "Lakshadweep" },
	{ code: "32", name: "Kerala" },
	{ code: "33", name: "Tamil Nadu" },
	{ code: "34", name: "Puducherry" },
	{ code: "35", name: "Andaman and Nicobar Islands" },
	{ code: "36", name: "Telangana" },
	{ code: "37", name: "Andhra Pradesh" },
	{ code: "38", name: "Ladakh" },
	{ code: "97", name: "Other Territory" },
];

export const stateName = (code: string | null | undefined): string =>
	GST_STATES.find((s) => s.code === code)?.name ?? "";

/** The state a GST number belongs to: its first two digits, or null while it is too short. */
export function stateOfGstin(gstin: string): string | null {
	const t = gstin.trim();
	return /^[0-9]{2}/.test(t) && GST_STATES.some((s) => s.code === t.slice(0, 2)) ? t.slice(0, 2) : null;
}
