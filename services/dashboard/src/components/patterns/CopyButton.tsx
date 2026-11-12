import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { en } from "@/lib/strings/en";

/**
 * Copies text; announces "Copied" through a polite live region. `ariaLabel` names what is copied
 * when several copy buttons share a page (it must contain the visible label).
 */
export function CopyButton({
	value,
	label = en.common.copy,
	ariaLabel,
}: {
	value: string;
	label?: string;
	ariaLabel?: string;
}) {
	const [copied, setCopied] = useState(false);
	return (
		<>
			<Button
				type="button"
				size="sm"
				aria-label={ariaLabel}
				onClick={async () => {
					try {
						await navigator.clipboard.writeText(value);
						setCopied(true);
						setTimeout(() => setCopied(false), 2000);
					} catch {
						setCopied(false);
					}
				}}
			>
				{copied ? <Check aria-hidden /> : <Copy aria-hidden />}
				{copied ? en.common.copied : label}
			</Button>
			<span className="sr-only" aria-live="polite">
				{copied ? en.common.copied : ""}
			</span>
		</>
	);
}
