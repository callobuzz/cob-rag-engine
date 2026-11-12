import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { PageHeader } from "@/components/shell/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InvoicesTab } from "@/features/invoices/admin/InvoicesTab"; // P2 invoices
import { PaymentsTab } from "@/features/payments/admin/PaymentsTab"; // P1 payments
import { TaxTab } from "@/features/payments/admin/TaxTab"; // P1 payments
import { ServiceOrdersTab } from "@/features/service-orders/ServiceOrdersTab"; // P5 plans
import { BILLING_TABS, type BillingTab } from "@/lib/search";
import { billingStrings as s } from "@/lib/strings/billing";
import { cn } from "@/lib/utils";
import { AccountsTab } from "./accounts/AccountsTab";
import { AddOnsTab } from "./addons/AddOnsTab"; // BIL5 add-ons
import { PnlTab } from "./pnl/PnlTab"; // BIL6 P&L
import { PricingTab } from "./pricing/PricingTab";

const route = getRouteApi("/_app/admin/billing");

/** `/admin/billing?tab=accounts|pricing` (super admin): how customers pay, and what each plan charges. */
export function BillingPage() {
	const { tab = "accounts" } = route.useSearch();
	const navigate = useNavigate();
	const panels: Record<BillingTab, React.ReactNode> = {
		accounts: <AccountsTab />,
		pricing: <PricingTab />,
		"add-ons": <AddOnsTab />, // BIL5 add-ons
		pnl: <PnlTab />, // BIL6 P&L
		payments: <PaymentsTab />, // P1 payments
		tax: <TaxTab />, // P1 payments
		invoices: <InvoicesTab />, // P2 invoices
		"service-orders": <ServiceOrdersTab />, // P5 plans
	};
	return (
		<>
			<PageHeader title={s.page.title} description={s.page.lead} />
			<Tabs
				value={tab}
				onValueChange={(v) => void navigate({ to: ".", search: (prev) => ({ ...prev, tab: v as BillingTab }) })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={s.page.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
				>
					{BILLING_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
							)}
						>
							{s.page.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				{BILLING_TABS.map((k) => (
					<TabsContent key={k} value={k} className="min-w-0">
						{tab === k ? panels[k] : null}
					</TabsContent>
				))}
			</Tabs>
		</>
	);
}
