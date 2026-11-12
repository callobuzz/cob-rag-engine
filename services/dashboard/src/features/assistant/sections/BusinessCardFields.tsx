import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { ChipsControl, InlineInput, SelectControl, TextControl, useFieldError } from "../controls";
import { DAYS, type Day, normalizePhone, prettyPhone } from "../schemas";

const MAX_PER_DAY = 3;

function zones(current: string): string[] {
	const all = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
	return [...new Set(["Asia/Kolkata", ...all, current].filter(Boolean))];
}

/** A phone field that says how the number will be saved. */
function PhoneControl({ name, label }: { name: string; label: string }) {
	const { control } = useFormContext();
	const value = useWatch({ control, name }) as string | null | undefined;
	const e164 = value ? normalizePhone(value) : null;
	return (
		<TextControl
			name={name}
			label={label}
			type="tel"
			inputMode="tel"
			autoComplete="off"
			hint={e164 ? fill(t.identity.savedAs, { pretty: prettyPhone(e164) }) : t.identity.phoneHint}
		/>
	);
}

/** Opening hours: a flat list in the form, drawn per weekday with up to three ranges each. */
function HoursEditor({ prefix }: { prefix: string }) {
	const { control, register } = useFormContext();
	const { fields, append, remove } = useFieldArray({ control, name: `${prefix}.hours` });
	const hours = (useWatch({ control, name: `${prefix}.hours` }) as { day: Day }[] | undefined) ?? [];
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.identity.hours}</legend>
			<small className="text-[12.5px] text-muted-foreground">{t.identity.hoursHint}</small>
			<ul className="m-0 grid list-none gap-1.5 p-0">
				{DAYS.map((day) => {
					const rows = fields.map((f, i) => ({ f, i })).filter(({ i }) => hours[i]?.day === day);
					return (
						<li
							key={day}
							className="grid items-center gap-2 rounded-md border border-border p-2 min-[700px]:grid-cols-[110px_minmax(0,1fr)_auto]"
						>
							<span className="text-[13.5px] font-semibold">{t.days[day]}</span>
							<div className="grid gap-1.5">
								{rows.length === 0 ? (
									<span className="text-[13px] text-muted-foreground">{t.identity.closed}</span>
								) : null}
								{rows.map(({ f, i }, n) => (
									<div key={f.id} className="flex flex-wrap items-start gap-2">
										<TimeBox
											label={fill(t.identity.opens, { day: t.days[day], n: n + 1 })}
											name={`${prefix}.hours.${i}.open`}
											register={register}
										/>
										<TimeBox
											label={fill(t.identity.closes, { day: t.days[day], n: n + 1 })}
											name={`${prefix}.hours.${i}.close`}
											register={register}
										/>
										<Button
											type="button"
											variant="ghost"
											size="sm"
											aria-label={fill(t.identity.removeHours, { day: t.days[day], n: n + 1 })}
											onClick={() => remove(i)}
										>
											{t.form.remove}
										</Button>
									</div>
								))}
							</div>
							<Button
								type="button"
								variant="outline"
								size="sm"
								aria-label={fill(t.identity.addHours, { day: t.days[day] })}
								disabled={rows.length >= MAX_PER_DAY || fields.length >= 21}
								onClick={() => append({ day, open: "09:00", close: "18:00" })}
							>
								{t.form.add}
							</Button>
						</li>
					);
				})}
			</ul>
		</fieldset>
	);
}

function TimeBox({
	label,
	name,
	register,
}: {
	label: string;
	name: string;
	register: ReturnType<typeof useFormContext>["register"];
}) {
	const error = useFieldError(name);
	return (
		<div className="grid gap-0.5">
			<input
				type="time"
				aria-label={label}
				aria-invalid={error ? true : undefined}
				className="h-[34px] rounded-sm border border-input bg-muted px-2 text-sm"
				{...register(name)}
			/>
			{error ? <small className="max-w-[220px] text-[12px] font-medium text-destructive">{error}</small> : null}
		</div>
	);
}

function HolidaysEditor({ prefix }: { prefix: string }) {
	const { control } = useFormContext();
	const { fields, append, remove } = useFieldArray({ control, name: `${prefix}.holidays` });
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.identity.holidays}</legend>
			<small className="text-[12.5px] text-muted-foreground">{t.identity.holidaysHint}</small>
			{fields.map((f, i) => (
				<div key={f.id} className="flex flex-wrap items-start gap-2">
					<InlineInput
						type="date"
						name={`${prefix}.holidays.${i}.date`}
						label={fill(t.identity.holidayDate, { n: i + 1 })}
					/>
					<InlineInput
						name={`${prefix}.holidays.${i}.name`}
						label={fill(t.identity.holidayName, { n: i + 1 })}
						maxLength={80}
						className="min-w-[180px] flex-1"
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						aria-label={fill(t.form.removeItem, { what: fill(t.identity.holidayName, { n: i + 1 }) })}
						onClick={() => remove(i)}
					>
						{t.form.remove}
					</Button>
				</div>
			))}
			<div>
				<Button
					type="button"
					variant="outline"
					size="sm"
					disabled={fields.length >= 60}
					onClick={() => append({ date: "", name: "" })}
				>
					{t.identity.addHoliday}
				</Button>
			</div>
		</fieldset>
	);
}

/** Every field of the business card. `prefix` is the card's path in the form ("card"). */
export function BusinessCardFields({ prefix = "card", timezone }: { prefix?: string; timezone: string }) {
	return (
		<div className="grid gap-4 p-4">
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.identity.cardNote}</p>
			<SelectControl
				name={`${prefix}.timezone`}
				label={t.identity.timezone}
				options={zones(timezone).map((z) => ({ value: z, label: z }))}
			/>
			<HoursEditor prefix={prefix} />
			<HolidaysEditor prefix={prefix} />
			<TextControl name={`${prefix}.address`} label={t.identity.address} rows={2} maxLength={300} counter />
			<div className="grid gap-4 min-[861px]:grid-cols-2">
				<PhoneControl name={`${prefix}.phone`} label={t.identity.phone} />
				<PhoneControl name={`${prefix}.whatsapp`} label={t.identity.whatsapp} />
				<TextControl name={`${prefix}.email`} label={t.identity.email} type="email" inputMode="email" />
				<TextControl name={`${prefix}.website`} label={t.identity.website} type="url" inputMode="url" />
				<TextControl name={`${prefix}.booking_url`} label={t.identity.booking} type="url" inputMode="url" />
			</div>
			<ChipsControl
				name={`${prefix}.service_areas`}
				label={t.identity.areas}
				hint={t.identity.areasHint}
				max={20}
				maxLength={80}
				addLabel={t.identity.areasAdd}
			/>
			<div className="grid gap-4 min-[861px]:grid-cols-2">
				<TextControl name={`${prefix}.payment_info`} label={t.identity.payments} maxLength={200} counter />
				<TextControl name={`${prefix}.refund_info`} label={t.identity.refunds} maxLength={200} counter />
			</div>
		</div>
	);
}
