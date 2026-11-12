import { File, FileAudio, FileCode, FileImage, FileSpreadsheet, FileText, type LucideIcon, Rows3 } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/** An icon for a document's type (records get the rows icon). */
export function mimeIcon(mime: string | null | undefined, isRecord = false): LucideIcon {
	if (isRecord) return Rows3;
	const m = mime ?? "";
	if (m.startsWith("audio/")) return FileAudio;
	if (m.startsWith("image/")) return FileImage;
	if (m.includes("spreadsheet") || m === "text/csv") return FileSpreadsheet;
	if (m === "text/html") return FileCode;
	if (m === "application/pdf" || m.includes("word") || m.includes("presentation") || m.startsWith("text/"))
		return FileText;
	return File;
}

/** A short, human type label: "PDF", "Word", "CSV"… */
export function mimeLabel(mime: string | null | undefined): string {
	const m = mime ?? "";
	const known: Record<string, string> = {
		"application/pdf": "PDF",
		"application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word",
		"application/vnd.openxmlformats-officedocument.presentationml.presentation": "PowerPoint",
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Excel",
		"text/csv": "CSV",
		"text/html": "HTML",
		"text/markdown": "Markdown",
		"text/plain": "Text",
		"image/png": "PNG image",
		"image/jpeg": "JPEG image",
	};
	if (known[m]) return known[m];
	if (m.startsWith("audio/")) return "Audio";
	return m || "—";
}

export function MimeIcon({
	mime,
	isRecord,
	className,
}: {
	mime: string | null | undefined;
	isRecord?: boolean;
	className?: string;
}) {
	const Icon = mimeIcon(mime, isRecord);
	return (
		<span
			className={cn("grid size-8 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground", className)}
		>
			<Icon className="size-4" aria-hidden />
		</span>
	);
}

/** `value` after it has stopped changing for `ms`. */
export function useDebouncedValue<T>(value: T, ms = 300): T {
	const [v, setV] = useState(value);
	useEffect(() => {
		const id = setTimeout(() => setV(value), ms);
		return () => clearTimeout(id);
	}, [value, ms]);
	return v;
}

/** Only http(s) URLs become links (README: operator URLs; same rule for document sources). */
export function safeHttpUrl(value: string | null | undefined): string | null {
	if (!value) return null;
	try {
		const u = new URL(value);
		return u.protocol === "https:" || u.protocol === "http:" ? u.toString() : null;
	} catch {
		return null;
	}
}

/** The business card is a system document kept by the assistant (`system:business-card`). */
export function isSystemDocument(d: { external_id?: string | null }): boolean {
	return !!d.external_id?.startsWith("system:");
}
