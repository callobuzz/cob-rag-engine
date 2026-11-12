import type * as React from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import type { Currency } from "@/lib/format/money";
import { billingStrings as s } from "@/lib/strings/billing";
import { cn } from "@/lib/utils";
import { type FieldDef, type FormValue, fieldLabel } from "./fields";

const SYMBOL: Record<Currency, string> = { INR: "₹", USD: "$" };

/** A text input with a currency symbol on its left (an amount) or a unit on its right. */
export function AdornedInput({
	symbol,
	unit,
	className,
	...props
}: React.ComponentProps<"input"> & { symbol?: string; unit?: string }) {
	return (
		<div className="relative">
			{symbol ? (
				<span
					aria-hidden
					className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sm text-muted-foreground"
				>
					{symbol}
				</span>
			) : null}
			<Input className={cn("font-mono", symbol && "pl-7", unit && "pr-9", className)} {...props} />
			{unit ? (
				<span
					aria-hidden
					className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-sm text-muted-foreground"
				>
					{unit}
				</span>
			) : null}
		</div>
	);
}

/**
 * One pricing field as its form control, controlled: money (₹/$ prefix), a markup percentage, a
 * whole number, a list of alert percentages, a choice or a checkbox. The plan's pricing dialog and
 * a customer's "Custom" terms both render fields through this, so both validate the same way.
 */
export function FieldControl({
	def,
	value,
	onChange,
	onBlur,
	currency,
	id,
	error,
	showLabel = true,
	ariaLabel,
	disabled,
}: {
	def: FieldDef;
	value: FormValue;
	onChange: (v: FormValue) => void;
	/** Marks the field touched (react-hook-form validates it on its first blur). */
	onBlur?: () => void;
	currency: Currency;
	id: string;
	error?: string | null;
	/** false: the surrounding row already shows the label; the control then gets `ariaLabel`. */
	showLabel?: boolean;
	ariaLabel?: string;
	disabled?: boolean;
}) {
	const text = typeof value === "string" ? value : "";
	const meta = s.fields[def.key] as { label: string; hint?: string };
	const hint = showLabel ? meta.hint : undefined;
	const aria = {
		"aria-invalid": !!error || undefined,
		"aria-describedby": describedBy(id, { hint: !!hint, error: !!error }),
		"aria-label": showLabel ? undefined : (ariaLabel ?? fieldLabel(def.key)),
	};

	if (def.kind === "bool") {
		return (
			<div className="flex items-start gap-2.5">
				<Checkbox
					id={id}
					className="mt-0.5"
					checked={value === true}
					disabled={disabled}
					aria-label={showLabel ? undefined : (ariaLabel ?? fieldLabel(def.key))}
					aria-describedby={meta.hint && showLabel ? `${id}-hint` : undefined}
					onCheckedChange={(c) => onChange(c === true)}
					onBlur={onBlur}
				/>
				{showLabel ? (
					<div className="grid gap-0.5">
						<label htmlFor={id} className="text-[13.5px] font-semibold">
							{meta.label}
						</label>
						{meta.hint ? (
							<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
								{meta.hint}
							</small>
						) : null}
					</div>
				) : null}
			</div>
		);
	}

	let control: React.ReactNode;
	if (def.kind === "select") {
		const labels = s.options[def.key as keyof typeof s.options] as Record<string, string> | undefined;
		// NativeSelect forwards props to the real <select>.
		control = (
			<NativeSelect
				id={id}
				value={text}
				disabled={disabled}
				onChange={(e) => onChange(e.target.value)}
				onBlur={onBlur}
				{...aria}
			>
				{def.options?.map((o) => (
					<option key={o} value={o}>
						{labels?.[o] ?? o}
					</option>
				))}
			</NativeSelect>
		);
	} else {
		control = (
			<AdornedInput
				id={id}
				value={text}
				disabled={disabled}
				autoComplete="off"
				inputMode={def.kind === "thresholds" ? "text" : "decimal"}
				symbol={def.kind === "money" ? SYMBOL[currency] : undefined}
				unit={def.kind === "bp" ? "%" : undefined}
				className={def.kind === "thresholds" ? "font-sans" : undefined}
				onChange={(e) => onChange(e.target.value)}
				onBlur={onBlur}
				{...aria}
			/>
		);
	}

	if (!showLabel) {
		return (
			<div className="grid gap-1">
				{control}
				{error ? (
					<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
						{error}
					</small>
				) : null}
			</div>
		);
	}
	return (
		<Field id={id} label={meta.label} hint={hint} error={error}>
			{control}
		</Field>
	);
}
