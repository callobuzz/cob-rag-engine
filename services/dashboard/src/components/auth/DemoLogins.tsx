import { DemoAnswers } from "@/components/shell/DemoAnswers";
import { Button } from "@/components/ui/button";
import type { DemoInfo, DemoLogin } from "@/lib/branding/demo";
import { demoStrings as t } from "@/lib/strings/demo";
import { fill } from "@/lib/strings/en";

const ROLE_LABEL: Record<DemoLogin["role"], string> = {
	operator: t.operatorRole,
	owner: t.ownerRole,
};

/**
 * The sign-ins of a live demo, under the sign-in form. They are public by design (the server
 * prints them for every visitor), so the password is shown as plain text. "Use" fills the form.
 */
export function DemoLogins({ info, onUse }: { info: DemoInfo; onUse: (login: DemoLogin) => void }) {
	return (
		<section
			aria-label={t.region}
			data-testid="demo-logins"
			className="mt-5 grid gap-3 rounded-lg border border-border bg-secondary p-4"
		>
			<div>
				<h2 className="m-0 text-[15px] font-bold">{t.title}</h2>
				<p className="m-0 mt-0.5 text-[13px] text-foreground-2">{t.lead}</p>
			</div>
			<ul className="m-0 grid list-none gap-2.5 p-0">
				{info.logins.map((login) => {
					const role = ROLE_LABEL[login.role];
					return (
						<li
							key={login.role}
							className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-md border border-border bg-card px-3 py-2.5"
						>
							<div className="min-w-0 text-[13px]">
								<div className="font-semibold">{role}</div>
								<div className="break-all font-mono text-[12.5px]">{login.email}</div>
								<div className="break-all font-mono text-[12.5px]">{login.password}</div>
							</div>
							<Button
								type="button"
								variant="outline"
								size="sm"
								aria-label={fill(t.rowLabel, { role })}
								onClick={() => onUse(login)}
							>
								{t.use}
							</Button>
						</li>
					);
				})}
			</ul>
			<p className="m-0 text-[12.5px] text-muted-foreground">
				{t.note} <DemoAnswers info={info} />
			</p>
		</section>
	);
}
