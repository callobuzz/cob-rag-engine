import { Link, useRouterState } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Sidebar link (mockup `.nav a`); `aria-current="page"` on the active item. */
export function NavItem({
	to,
	label,
	icon: Icon,
	badge,
	collapsed,
	onNavigate,
}: {
	to: string;
	label: string;
	icon: LucideIcon;
	badge?: ReactNode;
	collapsed?: boolean;
	onNavigate?: () => void;
}) {
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	const active = to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);
	return (
		<Link
			to={to}
			// TV6: the WhatsApp page is served with its own CSP (it loads Meta's sign-up SDK), and a
			// page's CSP is fixed when the document loads, so entering it is a real page load
			reloadDocument={to === "/whatsapp" || undefined}
			onClick={onNavigate}
			aria-current={active ? "page" : undefined}
			title={collapsed ? label : undefined}
			aria-label={collapsed ? label : undefined}
			className={cn(
				"flex h-[34px] items-center gap-2.5 overflow-hidden rounded-sm px-2.5 font-medium whitespace-nowrap text-foreground-2 no-underline hover:bg-accent hover:text-foreground",
				active && "bg-primary-soft text-foreground [&_svg]:text-primary",
				collapsed && "justify-center px-0",
			)}
		>
			<Icon className="size-[18px] shrink-0" aria-hidden />
			{collapsed ? null : <span className="truncate">{label}</span>}
			{!collapsed && badge ? <span className="ml-auto">{badge}</span> : null}
		</Link>
	);
}
