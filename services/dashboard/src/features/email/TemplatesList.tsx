import {
	Check,
	KeyRound,
	Lock,
	type LucideIcon,
	Mail,
	Pause,
	Send,
	TriangleAlert,
	UserPlus,
	Users,
	X,
} from "lucide-react";
import { Panel } from "@/features/kbs/ui";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { emailStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";

const t = emailStrings.templates;

const ICONS: Record<string, LucideIcon> = {
	mail: Mail,
	key: KeyRound,
	userplus: UserPlus,
	users: Users,
	check: Check,
	pause: Pause,
	x: X,
	lock: Lock,
	alert: TriangleAlert,
	send: Send,
};

/**
 * The e-mails the engine sends (A2's templates) with plain descriptions. Static: the engine keeps
 * no send history and offers no previews (AUDIT #56).
 */
export function TemplatesList() {
	const { productName } = useBranding();
	return (
		<Panel title={fill(t.title, { product: productName })} sub={t.sub}>
			<ul aria-label={t.label} className="m-0 grid list-none p-0">
				{t.items.map((item) => {
					const Icon = ICONS[item.icon] ?? Mail;
					return (
						<li
							key={item.id}
							className="flex min-w-0 items-center gap-3 border-b border-border px-4 py-[11px] last:border-b-0"
						>
							<Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
							<div className="min-w-0 flex-1">
								<div className="font-semibold">{item.t}</div>
								<div className="text-[12.5px] text-muted-foreground">{item.s}</div>
							</div>
						</li>
					);
				})}
			</ul>
		</Panel>
	);
}
