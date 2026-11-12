import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { PanelLeft, Settings } from "lucide-react";
import { BrandMark } from "@/components/brand/BrandMark";
import { Badge } from "@/components/ui/badge";
import { tenantCountQuery } from "@/features/customers/api";
import { supportStatsQuery } from "@/features/support-admin/api"; // TV3
import { corporaSwitcherQuery, infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useDashboardFeatures } from "@/lib/features"; // BIZ3
import { formatNumber } from "@/lib/format/number";
import { NAV_GROUPS, type PageMeta, pageLabel, tenantNav, visiblePages } from "@/lib/nav"; // BIZ3
import { customersStrings } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support"; // TV3
import { cn } from "@/lib/utils";
import { AdvancedNav } from "./AdvancedNav"; // BIZ3
import { NavItem } from "./NavItem";

/** Mockup `.sidebar`: brand (+ Admin pill), nav groups filtered by view, Settings + Collapse. */
export function Sidebar({
	collapsed = false,
	onToggleCollapse,
	onNavigate,
	inDrawer = false,
}: {
	collapsed?: boolean;
	onToggleCollapse?: () => void;
	onNavigate?: () => void;
	inDrawer?: boolean;
}) {
	const principal = usePrincipal();
	const { productName } = useBranding();
	const info = useQuery(infoQuery);
	const corpora = useQuery(corporaSwitcherQuery);
	const dashboard = useDashboardFeatures(); // BIZ3
	const pages = visiblePages(
		principal,
		{ content_portal: info.data?.features.content_portal, whatsapp: info.data?.features.whatsapp?.available },
		dashboard,
	);
	const isAdmin = principal?.view === "admin";
	const business = principal?.view === "tenant"; // BIZ3: the plain menu with a collapsed Advanced group
	const menu = tenantNav(pages); // BIZ3
	// D7: pending sign-ups (GET /admin/tenants?status=pending&limit=1 → total), every 60 s.
	const pending = useQuery({ ...tenantCountQuery("pending"), enabled: isAdmin, refetchInterval: 60_000 });

	// TV3: requests waiting for the provider, every 60 s
	const waiting = useQuery({ ...supportStatsQuery, enabled: isAdmin, refetchInterval: 60_000 });

	const badgeFor = (p: PageMeta) => {
		if (p.id === "support" && waiting.data && waiting.data.open > 0) {
			return (
				<Badge variant="warn" data-testid="support-count">
					<span aria-hidden>{formatNumber(waiting.data.open)}</span>
					<span className="sr-only"> {fill(supportStrings.admin.badge, { n: waiting.data.open })}</span>
				</Badge>
			);
		}
		if (p.id === "kbs" && corpora.data) return <Badge>{formatNumber(corpora.data.total ?? corpora.data.count)}</Badge>;
		if (p.id === "customers" && pending.data) {
			return (
				<Badge variant="warn" data-testid="pending-count">
					<span aria-hidden>{formatNumber(pending.data)}</span>
					<span className="sr-only"> {fill(customersStrings.list.pendingBadge, { count: pending.data })}</span>
				</Badge>
			);
		}
		return null;
	};

	return (
		<div className="flex h-full min-h-0 flex-col bg-muted">
			<div
				className={cn(
					"flex h-14 shrink-0 items-center gap-2 border-b border-border px-3.5",
					collapsed && "justify-center px-0",
				)}
			>
				<Link
					to="/"
					onClick={onNavigate}
					className="flex min-w-0 items-center gap-2.5 font-display text-base font-extrabold tracking-[-.02em] text-foreground no-underline"
					aria-label={collapsed ? productName : undefined}
				>
					<BrandMark />
					{collapsed ? null : <span className="truncate">{productName}</span>}
				</Link>
				{isAdmin && !collapsed ? (
					<span className="rounded-full border border-[color-mix(in_srgb,var(--warning)_50%,transparent)] px-1.5 font-mono text-[9.5px] font-medium tracking-[.08em] text-warning uppercase">
						{en.shell.adminPill}
					</span>
				) : null}
			</div>
			<nav aria-label={en.shell.mainNav} className="grid flex-1 content-start gap-0.5 overflow-y-auto px-2 py-2.5">
				{/* BIZ3 */}
				{business ? (
					<>
						<div className="grid gap-0.5">
							{menu.top.map((p) => (
								<NavItem
									key={p.id}
									to={p.path}
									label={pageLabel(p, "tenant")}
									icon={p.icon}
									badge={badgeFor(p)}
									collapsed={collapsed}
									onNavigate={onNavigate}
								/>
							))}
						</div>
						<AdvancedNav
							pages={menu.advanced}
							collapsed={collapsed}
							render={(p) => (
								<NavItem
									key={p.id}
									to={p.path}
									label={pageLabel(p, "tenant")}
									icon={p.icon}
									badge={badgeFor(p)}
									collapsed={collapsed}
									onNavigate={onNavigate}
								/>
							)}
						/>
					</>
				) : null}
				{/* /BIZ3 */}
				{(business ? [] : NAV_GROUPS).map((group) => {
					const items = pages.filter((p) => p.group === group);
					if (items.length === 0) return null;
					return (
						<div key={group || "top"} className="grid gap-0.5">
							{group && !collapsed ? <div className="nav-label px-2.5 pt-3 pb-1">{group}</div> : null}
							{group && collapsed ? <div className="mx-3 my-2 border-t border-border" /> : null}
							{items.map((p) => (
								<NavItem
									key={p.id}
									to={p.path}
									label={p.label}
									icon={p.icon}
									badge={badgeFor(p)}
									collapsed={collapsed}
									onNavigate={onNavigate}
								/>
							))}
						</div>
					);
				})}
			</nav>
			<div className="grid gap-1 border-t border-border px-2 py-2.5">
				<NavItem
					to="/settings"
					label={en.shell.settings}
					icon={Settings}
					collapsed={collapsed}
					onNavigate={onNavigate}
				/>
				{inDrawer ? null : (
					<button
						type="button"
						onClick={onToggleCollapse}
						aria-label={collapsed ? en.shell.expandSidebar : en.shell.collapseSidebar}
						aria-expanded={!collapsed}
						className={cn(
							"flex h-[34px] items-center gap-2.5 rounded-sm px-2.5 text-muted-foreground hover:bg-accent hover:text-foreground",
							collapsed && "justify-center px-0",
						)}
					>
						<PanelLeft className="size-[18px]" aria-hidden />
						{collapsed ? null : <span>{en.shell.collapse}</span>}
					</button>
				)}
			</div>
		</div>
	);
}
