import { Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type Currency, formatMinor, minorToInput, parseMajor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingContent, usePlanChoices } from "./api";
import { useDraft } from "./DraftProvider";
import type { LText } from "./draft";
import { Fields, type Rec } from "./fields";
import { LocalizedField } from "./LocalizedField";
import { PRICING_FIELDS } from "./pageSpecs";
import type { FieldSpec } from "./sections";

type Pricing = LandingContent["pricing"];
type Card = NonNullable<Pricing["cards"]>[number];
type Period = "monthly" | "yearly";
const CURRENCIES: Currency[] = ["INR", "USD"];
const PERIODS: Period[] = ["monthly", "yearly"];

const CALCULATOR_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show the price calculator" },
	{ k: "number", key: "slider_max", label: "Most questions a month the slider goes to" },
	{ k: "number", key: "slider_default", label: "Where the slider starts" },
];

/** What one answer is estimated to cost, per currency, typed in rupees or dollars and sent as whole paise or cents. */
function AnswerPrice({
	currency,
	minor,
	onChange,
}: {
	currency: Currency;
	minor: number | undefined;
	onChange: (minor: number | undefined) => void;
}) {
	const id = useId();
	const [text, setText] = useState(minor == null ? "" : minorToInput(minor));
	useEffect(() => setText(minor == null ? "" : minorToInput(minor)), [minor]);
	const parsed = text.trim() === "" ? undefined : parseMajor(text, currency);
	const invalid = text.trim() !== "" && (parsed == null || parsed < 1);
	return (
		<div className="grid content-start gap-1.5">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{fill(t.pricing.answerPrice, { currency: currency === "INR" ? "₹" : "$" })}
			</label>
			<Input
				id={id}
				inputMode="decimal"
				className="max-w-40"
				value={text}
				aria-invalid={invalid || undefined}
				aria-describedby={`${id}-hint`}
				onChange={(e) => {
					setText(e.target.value);
					const next = e.target.value.trim() === "" ? undefined : parseMajor(e.target.value, currency);
					if (e.target.value.trim() === "" || (next != null && next >= 1)) onChange(next ?? undefined);
				}}
			/>
			<small
				id={`${id}-hint`}
				className={invalid ? "text-[12.5px] text-destructive" : "text-[12.5px] text-muted-foreground"}
			>
				{invalid
					? t.pricing.answerPriceInvalid
					: minor != null
						? formatMinor(minor, currency)
						: t.pricing.answerPriceHint}
			</small>
		</div>
	);
}

function CardEditor({
	card,
	index,
	onChange,
	onRemove,
}: {
	card: Card;
	index: number;
	onChange: (next: Card) => void;
	onRemove: () => void;
}) {
	const plans = usePlanChoices();
	const base = `pricing.cards[${index}]`;
	const setPlan = (currency: Currency, period: Period, id: string) => {
		const variants = structuredClone(card.variants ?? {}) as Record<string, Record<string, string>>;
		const row = { ...(variants[currency] ?? {}) };
		if (id === "") delete row[period];
		else row[period] = id;
		if (Object.keys(row).length === 0) delete variants[currency];
		else variants[currency] = row;
		onChange({ ...card, variants } as Card);
	};
	const variants = (card.variants ?? {}) as Record<string, Record<string, string>>;
	const empty = Object.keys(variants).length === 0;
	return (
		<div className="grid gap-3 rounded-sm border border-border bg-secondary/40 p-3" data-testid={`price-card-${index}`}>
			<div className="flex items-center justify-between gap-2">
				<strong className="text-[13px]">{fill(t.pricing.card, { n: index + 1 })}</strong>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={fill(t.list.remove, { item: fill(t.pricing.card, { n: index + 1 }) })}
					onClick={onRemove}
				>
					<Trash2 aria-hidden />
				</Button>
			</div>
			<div className="grid gap-3 min-[861px]:grid-cols-2">
				<LocalizedField
					path={`${base}.label`}
					label={t.pricing.cardLabel}
					value={card.label as LText | null}
					onChange={(v) => onChange({ ...card, label: v } as Card)}
					limitKey="pricing.cards.label"
				/>
				<LocalizedField
					path={`${base}.who_for`}
					label={t.pricing.whoFor}
					value={card.who_for as LText}
					onChange={(v) => onChange({ ...card, who_for: v ?? { en: "" } } as Card)}
					limitKey="pricing.cards.who_for"
					required
				/>
				<LocalizedField
					path={`${base}.highlight`}
					label={t.pricing.highlight}
					value={card.highlight as LText | null}
					onChange={(v) => onChange({ ...card, highlight: v } as Card)}
					limitKey="pricing.cards.highlight"
					hint={t.pricing.highlightHint}
				/>
				<div className="grid content-start gap-1.5">
					<label htmlFor={`${base}-cta`} className="text-[13px] font-semibold">
						{t.pricing.cta}
					</label>
					<NativeSelect
						id={`${base}-cta`}
						className="max-w-xs"
						value={card.cta ?? "signup"}
						onChange={(e) => onChange({ ...card, cta: e.target.value as Card["cta"] })}
					>
						<option value="signup">{t.pricing.ctaSignup}</option>
						<option value="contact">{t.pricing.ctaContact}</option>
					</NativeSelect>
				</div>
			</div>
			<table className="w-full border-collapse text-left text-[13px]">
				<caption className="mb-1 text-left text-[13px] font-semibold">{t.pricing.plansFor}</caption>
				<thead>
					<tr className="text-muted-foreground">
						<th className="py-1 pr-2 font-medium">{t.pricing.currency}</th>
						<th className="py-1 pr-2 font-medium">{t.pricing.monthly}</th>
						<th className="py-1 pr-2 font-medium">{t.pricing.yearly}</th>
					</tr>
				</thead>
				<tbody>
					{CURRENCIES.map((currency) => (
						<tr key={currency} className="border-t border-border">
							<th scope="row" className="py-1.5 pr-2 font-semibold">
								{currency === "INR" ? t.pricing.inr : t.pricing.usd}
							</th>
							{PERIODS.map((period) => (
								<td key={period} className="py-1.5 pr-2">
									<NativeSelect
										aria-label={`${currency === "INR" ? t.pricing.inr : t.pricing.usd}, ${t.pricing[period]}`}
										value={variants[currency]?.[period] ?? ""}
										onChange={(e) => setPlan(currency, period, e.target.value)}
									>
										<option value="">{t.pricing.notSold}</option>
										{plans.data?.map((p) => (
											<option key={p.id} value={p.id}>
												{p.name}
											</option>
										))}
									</NativeSelect>
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
			{empty ? (
				<p role="note" className="m-0 text-[12.5px] text-warning">
					{t.pricing.needsPlan}
				</p>
			) : null}
		</div>
	);
}

/** The pricing page: which plans become cards, the calculator, the credit explanation and the questions. */
export function PricingTab() {
	const draft = useDraft();
	const pricing = draft.content.pricing as unknown as Rec;
	const cards = (draft.content.pricing.cards ?? []) as Card[];
	const calc = (draft.content.pricing.calculator ?? {}) as Rec & {
		est_answer_minor?: Partial<Record<Currency, number>>;
	};
	const placeholder = draft.state.warnings.find((w) => w.code === "placeholder_price");

	const setPricing = (next: Rec) =>
		draft.update((d) => {
			d.pricing = next as unknown as Pricing;
		});
	const setCards = (next: Card[]) =>
		draft.update((d) => {
			d.pricing.cards = next;
		});
	const setAnswer = (currency: Currency, minor: number | undefined) =>
		draft.update((d) => {
			const est = { ...(d.pricing.calculator?.est_answer_minor ?? {}) } as Record<string, number>;
			if (minor == null) delete est[currency];
			else est[currency] = minor;
			d.pricing.calculator = { ...(d.pricing.calculator as object), est_answer_minor: est } as Pricing["calculator"];
		});

	return (
		<div className="grid gap-4" data-testid="pricing-tab">
			{placeholder ? (
				<p
					role="status"
					data-testid="placeholder-price"
					className="m-0 rounded-sm bg-warning-soft p-3 text-[13px] text-warning"
				>
					{placeholder.message}
				</p>
			) : null}
			<section aria-label={t.pricing.pageTitle} className="grid gap-4 rounded-lg border border-border bg-card p-4">
				<h2 className="m-0 text-[15px] font-bold">{t.pricing.pageTitle}</h2>
				<Fields specs={PRICING_FIELDS} value={pricing} onChange={setPricing} path="pricing" limitBase="pricing" />
			</section>
			<section aria-label={t.pricing.cardsTitle} className="grid gap-3 rounded-lg border border-border bg-card p-4">
				<div className="grid gap-0.5">
					<h2 className="m-0 text-[15px] font-bold">{t.pricing.cardsTitle}</h2>
					<p className="m-0 text-[13px] text-muted-foreground">{t.pricing.cardsAbout}</p>
				</div>
				{cards.map((card, i) => (
					<CardEditor
						// biome-ignore lint/suspicious/noArrayIndexKey: cards have no id; their place is their identity
						key={i}
						card={card}
						index={i}
						onChange={(next) => setCards(cards.map((c, j) => (j === i ? next : c)))}
						onRemove={() => setCards(cards.filter((_, j) => j !== i))}
					/>
				))}
				{cards.length < 4 ? (
					<div>
						<Button
							type="button"
							size="sm"
							variant="outline"
							data-testid="add-price-card"
							onClick={() =>
								setCards([...cards, { who_for: { en: "" }, variants: {}, cta: "signup" } as unknown as Card])
							}
						>
							<Plus aria-hidden />
							{t.pricing.addCard}
						</Button>
					</div>
				) : null}
			</section>
			<section aria-label={t.pricing.calcTitle} className="grid gap-4 rounded-lg border border-border bg-card p-4">
				<h2 className="m-0 text-[15px] font-bold">{t.pricing.calcTitle}</h2>
				<Fields
					specs={CALCULATOR_FIELDS}
					value={calc}
					onChange={(next) => setPricing({ ...pricing, calculator: next })}
					path="pricing.calculator"
					limitBase="pricing.calculator"
				/>
				<div className="grid gap-3 min-[600px]:grid-cols-2">
					{CURRENCIES.map((c) => (
						<AnswerPrice key={c} currency={c} minor={calc.est_answer_minor?.[c]} onChange={(m) => setAnswer(c, m)} />
					))}
				</div>
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.pricing.calcAbout}</p>
			</section>
		</div>
	);
}
