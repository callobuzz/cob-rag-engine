import { KeyRound, TriangleAlert } from "lucide-react";
import { CopyButton } from "./CopyButton";

/** A secret shown once (new keys): mono value, copy button, "store it now" warning. */
export function SecretReveal({ value, label = "New key" }: { value: string; label?: string }) {
	return (
		<div className="grid gap-2">
			<div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-muted p-3">
				<KeyRound className="text-muted-foreground" aria-hidden />
				<div className="min-w-[180px] flex-1">
					<div className="text-xs text-muted-foreground">{label}</div>
					<code className="break-all">{value}</code>
				</div>
				<CopyButton value={value} />
			</div>
			<p className="m-0 flex items-start gap-2 text-[13px] text-warning">
				<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
				Store it now — it won't be shown again.
			</p>
		</div>
	);
}
