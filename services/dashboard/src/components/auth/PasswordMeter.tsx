import { en, fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

/**
 * Advisory 4-step meter (D1 decision). Only length against the policy blocks a submit; variety
 * and length ≥ 16 raise the score; the server's `weak_password` reasons are authoritative.
 */
/** Length in code points after NFKC, the way the engine counts it (accounts/passwords.py). */
export function passwordLength(pw: string): number {
	return [...pw.normalize("NFKC")].length;
}

export function passwordScore(pw: string, policy: { min_length: number; max_length: number }): number {
	const len = passwordLength(pw);
	if (len < policy.min_length || len > policy.max_length) return 0;
	const variety = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
	let score = 1;
	if (variety >= 3 || len >= 14) score++;
	if (len >= 16) score++;
	if (len >= 20 || (len >= 16 && variety >= 3)) score++;
	return Math.min(4, score);
}

export function passwordLengthOk(pw: string, policy: { min_length: number; max_length: number }): boolean {
	const len = passwordLength(pw);
	return len >= policy.min_length && len <= policy.max_length;
}

const COLORS = ["", "bg-destructive", "bg-warning", "bg-primary", "bg-success"];

export function PasswordMeter({
	password,
	policy,
	id,
}: {
	password: string;
	policy: { min_length: number; max_length: number };
	id?: string;
}) {
	const score = passwordScore(password, policy);
	const tooLong = passwordLength(password) > policy.max_length;
	const label =
		score === 0
			? tooLong
				? `Use at most ${policy.max_length} characters`
				: fill(en.password.useAtLeast, { min: policy.min_length })
			: (en.password.strength[score] ?? "");
	return (
		// biome-ignore lint/a11y/useSemanticElements: <meter> cannot hold the four segment bars and label; role=meter is spec'd
		<div
			id={id}
			role="meter"
			aria-label="Password strength"
			aria-valuemin={0}
			aria-valuemax={4}
			aria-valuenow={score}
			aria-valuetext={label}
			className="flex items-center gap-1"
		>
			{[1, 2, 3, 4].map((i) => (
				<i key={i} className={cn("h-1 flex-1 rounded-[2px] bg-input transition-colors", i <= score && COLORS[score])} />
			))}
			<span className="ml-2 min-w-[104px] text-right text-xs text-muted-foreground">{label}</span>
		</div>
	);
}
