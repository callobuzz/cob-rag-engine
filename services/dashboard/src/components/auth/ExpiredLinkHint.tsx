import { CopyButton } from "@/components/patterns/CopyButton";
import { en } from "@/lib/strings/en";

/**
 * Under the "code expired or used up" alert on the super-admin set-password page. People who open
 * that link are usually not the one who runs the server, so the first line tells them what to do;
 * the operator (often the same person on a first install) opens the disclosure for the command.
 */
export function ExpiredLinkHint() {
	return (
		<div className="grid gap-2 text-[13px] text-foreground-2" data-testid="expired-link-hint">
			<p className="m-0 font-medium">{en.setPassword.expiredHint}</p>
			<details className="group rounded-sm border border-border bg-card px-3 py-2">
				<summary className="cursor-pointer text-[13px] font-semibold">{en.setPassword.runningThis}</summary>
				<div className="mt-2 grid gap-2">
					<p className="m-0">{en.setPassword.runningBody}</p>
					<div className="flex flex-wrap items-center gap-2">
						<code className="min-w-0 flex-1 overflow-x-auto rounded-sm bg-muted px-2 py-1.5 font-mono text-[12px] whitespace-nowrap">
							{en.setPassword.resendCommand}
						</code>
						<CopyButton value={en.setPassword.resendCommand} ariaLabel="Copy the resend command" />
					</div>
				</div>
			</details>
		</div>
	);
}
