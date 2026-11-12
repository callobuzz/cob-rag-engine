/** `INV/26-27/00001` → `INV-26-27-00001.pdf`, the name the engine sends in Content-Disposition. */
export function pdfFileName(number: string): string {
	return `${number.replace(/\//g, "-")}.pdf`;
}

/** Save a blob under a file name (the browser's download, no navigation). */
export function saveBlob(blob: Blob, fileName: string): void {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = fileName;
	document.body.append(a);
	a.click();
	a.remove();
	setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** First and last day of a "YYYY-MM" month (the CSV range the accountant files a return for). */
export function monthRange(month: string): { start: string; end: string } {
	const [y, m] = month.split("-").map(Number);
	const year = y ?? 1970;
	const mon = m ?? 1;
	const last = new Date(Date.UTC(year, mon, 0)).getUTCDate();
	const pad = (n: number) => String(n).padStart(2, "0");
	return { start: `${year}-${pad(mon)}-01`, end: `${year}-${pad(mon)}-${pad(last)}` };
}
