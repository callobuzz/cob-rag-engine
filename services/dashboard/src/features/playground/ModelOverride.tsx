import { useId } from "react";
import { Input } from "@/components/ui/input";
import { playgroundStrings as t } from "@/lib/strings/playground";
import type { LlmOverride } from "./api";

/**
 * "Advanced → Use a different model for this question" (BYOK test): provider, model, API key and
 * base URL, sent as X-LLM-* headers. Held in React state only — never written to storage, cleared
 * on reload, never logged.
 */
export function ModelOverride({ value, onChange }: { value: LlmOverride; onChange: (v: LlmOverride) => void }) {
	const id = useId();
	const field = (key: keyof LlmOverride, label: string, props: React.ComponentProps<typeof Input> = {}) => (
		<div className="grid min-w-0 gap-1.5">
			<label htmlFor={`${id}-${key}`} className="text-[13px] font-semibold">
				{label}
			</label>
			<Input
				id={`${id}-${key}`}
				value={value[key]}
				autoComplete="off"
				spellCheck={false}
				onChange={(e) => onChange({ ...value, [key]: e.target.value })}
				{...props}
			/>
		</div>
	);
	return (
		<details className="group rounded-md border border-border bg-card">
			<summary className="cursor-pointer px-3 py-2 text-[13px] font-semibold select-none">{t.try.byokSummary}</summary>
			<div className="grid gap-3 border-t border-border p-3">
				<p className="m-0 text-xs text-muted-foreground">{t.try.byokNote}</p>
				<div className="grid gap-3 min-[861px]:grid-cols-2">
					{field("provider", t.try.provider, { placeholder: t.try.providerPlaceholder })}
					{field("model", t.try.modelId, { placeholder: t.try.modelPlaceholder })}
					{field("apiKey", t.try.apiKey, { type: "password" })}
					{field("baseUrl", t.try.baseUrl, { type: "url", placeholder: "https://" })}
				</div>
			</div>
		</details>
	);
}
