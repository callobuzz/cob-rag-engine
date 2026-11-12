import { type DemoInfo, localResetTime } from "@/lib/branding/demo";
import { demoStrings as t } from "@/lib/strings/demo";
import { fill } from "@/lib/strings/en";

/**
 * "X of Y demo answers left today", or, when none are left, that they are used up and when the
 * demo resets. Numbers only: the engine never sends a visitor's address. Nothing when the engine
 * could not read the counter (the banner and the sign-in page just say less).
 */
export function DemoAnswers({ info, className }: { info: DemoInfo; className?: string }) {
	const answers = info.answers;
	if (!answers) return null;
	const used = answers.left <= 0;
	const text = used
		? fill(t.answersUsedUp, { time: localResetTime(info.resets_at_utc) })
		: fill(t.answersLeft, { left: answers.left, limit: answers.limit });
	return (
		<span
			data-testid="demo-answers"
			data-used-up={used ? "true" : "false"}
			className={[className, used ? "font-semibold" : ""].filter(Boolean).join(" ")}
		>
			{text}
		</span>
	);
}
