import type { ReactNode } from "react";

/** Mockup `.field`: visible label, control, hint, error (linked by aria-describedby by the caller). */
export function Field({
	id,
	label,
	labelExtra,
	hint,
	error,
	children,
}: {
	id: string;
	label: string;
	labelExtra?: ReactNode;
	hint?: ReactNode;
	error?: string | null;
	children: ReactNode;
}) {
	return (
		<div className="grid content-start gap-1.5">
			<div className="flex items-baseline justify-between gap-3">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{label}
				</label>
				{labelExtra}
			</div>
			{children}
			{hint ? (
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{hint}
				</small>
			) : null}
			{error ? (
				<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
					{error}
				</small>
			) : null}
		</div>
	);
}

/** aria-describedby for a Field's control. */
export function describedBy(id: string, opts: { hint?: boolean; error?: boolean }): string | undefined {
	const ids = [opts.hint ? `${id}-hint` : null, opts.error ? `${id}-error` : null].filter(Boolean);
	return ids.length ? ids.join(" ") : undefined;
}
