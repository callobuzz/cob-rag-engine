import { Plus, Trash2 } from "lucide-react";
import { useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Day = (typeof DAYS)[number];
export interface HoursRow {
	day: Day;
	open: string;
	close: string;
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/**
 * What is wrong with a list of opening hours, in the engine's own terms (`DayHours`): both times
 * are HH:MM, and they differ. A closing time before the opening time is fine: it closes the next
 * day (a restaurant open 18:00 to 02:00).
 */
export function hoursProblem(rows: readonly HoursRow[]): string | null {
	for (const r of rows) {
		if (!HHMM.test(r.open) || !HHMM.test(r.close)) return t.proposal.timesInvalid;
		if (r.open === r.close) return t.proposal.timesSame;
	}
	return null;
}

export const isHoursRow = (v: unknown): v is HoursRow =>
	!!v &&
	typeof v === "object" &&
	DAYS.includes((v as HoursRow).day) &&
	typeof (v as HoursRow).open === "string" &&
	typeof (v as HoursRow).close === "string";

/** Rows of day, opens, closes. "Same for Mon–Sat" copies the first row to the other weekdays. */
export function HoursEditor({
	value,
	onChange,
	error,
	idPrefix,
}: {
	value: HoursRow[];
	onChange: (next: HoursRow[]) => void;
	error?: string | null;
	idPrefix?: string;
}) {
	const generated = useId();
	const base = idPrefix ?? generated;
	const update = (i: number, patch: Partial<HoursRow>) =>
		onChange(value.map((r, k) => (k === i ? { ...r, ...patch } : r)));
	const first = value[0];
	const sameForWeek = () => {
		if (!first) return;
		const week = DAYS.slice(0, 6).map((day) => ({ day, open: first.open, close: first.close }));
		onChange([...week, ...value.filter((r) => r.day === "sun")]);
	};
	return (
		<div className="grid gap-2">
			{value.length ? (
				<ul className="m-0 grid list-none gap-2 p-0">
					{value.map((r, i) => (
						// biome-ignore lint/suspicious/noArrayIndexKey: rows are edited in place; day is not unique
						<li key={i} className="flex flex-wrap items-end gap-2">
							<div className="grid gap-1">
								<label htmlFor={`${base}-day-${i}`} className="text-[12px] font-semibold">
									{t.proposal.day}
								</label>
								<NativeSelect
									id={`${base}-day-${i}`}
									value={r.day}
									onChange={(e) => update(i, { day: e.target.value as Day })}
									className="w-[96px]"
								>
									{DAYS.map((d) => (
										<option key={d} value={d}>
											{t.proposal.days[d]}
										</option>
									))}
								</NativeSelect>
							</div>
							<div className="grid gap-1">
								<label htmlFor={`${base}-open-${i}`} className="text-[12px] font-semibold">
									{t.proposal.opens}
								</label>
								<Input
									id={`${base}-open-${i}`}
									value={r.open}
									onChange={(e) => update(i, { open: e.target.value.trim() })}
									placeholder="09:00"
									maxLength={5}
									inputMode="numeric"
									className="w-[84px]"
									aria-invalid={error ? true : undefined}
								/>
							</div>
							<div className="grid gap-1">
								<label htmlFor={`${base}-close-${i}`} className="text-[12px] font-semibold">
									{t.proposal.closes}
								</label>
								<Input
									id={`${base}-close-${i}`}
									value={r.close}
									onChange={(e) => update(i, { close: e.target.value.trim() })}
									placeholder="18:00"
									maxLength={5}
									inputMode="numeric"
									className="w-[84px]"
									aria-invalid={error ? true : undefined}
								/>
							</div>
							{HHMM.test(r.open) && HHMM.test(r.close) && r.close < r.open ? (
								<span className="pb-2 text-[12.5px] text-muted-foreground">{t.proposal.nextDay}</span>
							) : null}
							<Button
								type="button"
								variant="ghost"
								size="icon-sm"
								aria-label={fill(t.proposal.removeHours, { day: t.proposal.days[r.day] })}
								onClick={() => onChange(value.filter((_, k) => k !== i))}
							>
								<Trash2 aria-hidden />
							</Button>
						</li>
					))}
				</ul>
			) : null}
			<div className="flex flex-wrap gap-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={() => onChange([...value, { day: DAYS[value.length % 7] ?? "mon", open: "09:00", close: "18:00" }])}
				>
					<Plus aria-hidden />
					{t.proposal.addHours}
				</Button>
				{first ? (
					<Button type="button" variant="ghost" size="sm" onClick={sameForWeek}>
						{t.proposal.sameForWeek}
					</Button>
				) : null}
			</div>
			{error ? (
				<small className="text-[12.5px] font-medium text-destructive" role="alert">
					{error}
				</small>
			) : null}
		</div>
	);
}
