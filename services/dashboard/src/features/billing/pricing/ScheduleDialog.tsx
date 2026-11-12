import { useId, useState } from "react";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";

const s = noSurprisesStrings.schedule;

const IST = "Asia/Kolkata";

/** The Indian calendar day (`YYYY-MM-DD`) of an instant: how a start date is shown and chosen. */
export function istDay(iso: string): string {
	return new Date(iso).toLocaleDateString("en-CA", { timeZone: IST });
}

/** A chosen day as the instant the engine expects: midnight in India. */
export function startOfDayIst(day: string): string {
	return `${day}T00:00:00+05:30`;
}

/** "5 November 2026" for a `YYYY-MM-DD` day. */
export function longDay(day: string): string {
	return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-IN", {
		day: "numeric",
		month: "long",
		year: "numeric",
		timeZone: "UTC",
	});
}

/**
 * Announce a price rise (TV3): how many businesses will pay more, the date it starts (never before
 * the earliest day the notice period allows) and a button that schedules it and e-mails the owners.
 */
export function ScheduleDialog({
	open,
	workspaces,
	earliest,
	noticeDays,
	replaces,
	pending,
	error,
	onConfirm,
	onBack,
}: {
	open: boolean;
	workspaces: number;
	/** The earliest instant a rise may start (`earliest_effective_at` from the preview). */
	earliest: string;
	noticeDays?: number;
	/** A change is already scheduled for the plan and will be replaced. */
	replaces?: boolean;
	pending: boolean;
	error?: string | null;
	onConfirm: (effectiveAt: string) => void;
	onBack: () => void;
}) {
	const id = useId();
	const min = istDay(earliest);
	const [day, setDay] = useState(min);
	const tooEarly = day < min;
	return (
		<Dialog open={open} onOpenChange={(o) => (o ? undefined : onBack())}>
			<DialogContent className="sm:max-w-[520px]" data-testid="schedule-dialog">
				<form
					noValidate
					className="grid gap-4"
					onSubmit={(e) => {
						e.preventDefault();
						if (!tooEarly && day) onConfirm(startOfDayIst(day));
					}}
				>
					<DialogHeader>
						<DialogTitle>{s.title}</DialogTitle>
						<DialogDescription data-testid="schedule-lead">
							{workspaces === 1 ? s.leadOne : fill(s.lead, { count: workspaces })}
						</DialogDescription>
					</DialogHeader>
					{error ? <FormAlert>{error}</FormAlert> : null}
					<Field
						id={id}
						label={s.date}
						hint={fill(s.dateHint, { date: longDay(min), days: noticeDays ?? 30 })}
						error={tooEarly ? fill(s.dateError, { date: longDay(min) }) : null}
					>
						<Input
							id={id}
							type="date"
							min={min}
							value={day}
							onChange={(e) => setDay(e.target.value)}
							aria-describedby={`${id}-hint${tooEarly ? ` ${id}-error` : ""}`}
							aria-invalid={tooEarly || undefined}
						/>
					</Field>
					{replaces ? <p className="m-0 text-[12.5px] text-muted-foreground">{s.replaces}</p> : null}
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={onBack}>
							{s.back}
						</Button>
						<SubmitButton variant="default" pending={pending} pendingLabel={s.confirming}>
							{s.confirm}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
