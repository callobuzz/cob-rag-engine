import { Search, X } from "lucide-react";
import { type RefObject, useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { docsStrings as t } from "@/lib/strings/docs";
import { SEARCH_MAX } from "./api";

const DEBOUNCE_MS = 250;

/** True when a key press belongs to a field the person is typing in, not to the page. */
function typingInField(target: EventTarget | null): boolean {
	const el = target as HTMLElement | null;
	if (!el) return false;
	return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
}

/**
 * The search box. `/` focuses it from anywhere on the page. The typed text goes to the address
 * (`?q=`) after a short pause, so a search can be shared and Back returns to the page.
 */
export function DocsSearch({
	q,
	onQuery,
	inputRef,
}: {
	q: string;
	onQuery: (q: string) => void;
	inputRef: RefObject<HTMLInputElement | null>;
}) {
	const [value, setValue] = useState(q);

	// The address changed from outside (Back, a link): follow it.
	useEffect(() => setValue(q), [q]);

	useEffect(() => {
		if (value.trim() === q.trim()) return;
		const id = setTimeout(() => onQuery(value), DEBOUNCE_MS);
		return () => clearTimeout(id);
	}, [value, q, onQuery]);

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || typingInField(e.target)) return;
			e.preventDefault();
			inputRef.current?.focus();
		};
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [inputRef]);

	return (
		<search className="block">
			<form
				className="relative"
				onSubmit={(e) => {
					e.preventDefault();
					onQuery(value);
				}}
			>
				<Search
					className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
					aria-hidden
				/>
				<Input
					ref={inputRef}
					type="search"
					value={value}
					maxLength={SEARCH_MAX}
					onChange={(e) => setValue(e.target.value)}
					aria-label={t.searchLabel}
					placeholder={t.searchPlaceholder}
					title={t.searchHint}
					className="pr-9 pl-9"
					autoComplete="off"
				/>
				{value ? (
					<button
						type="button"
						aria-label={t.searchClear}
						className="absolute top-1/2 right-2 grid size-6 -translate-y-1/2 place-items-center rounded-sm text-muted-foreground hover:text-foreground"
						onClick={() => {
							setValue("");
							onQuery("");
							inputRef.current?.focus();
						}}
					>
						<X className="size-4" aria-hidden />
					</button>
				) : null}
			</form>
		</search>
	);
}
