import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Home, UserPlus } from "lucide-react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { useSwitchScope } from "@/components/shell/ScopeSwitcher";
import { Button, buttonVariants } from "@/components/ui/button";
import { activeCustomersQuery } from "@/features/kbs/api";
import { operatorWorkspace } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { kbsStrings } from "@/lib/strings/kbs";

const t = kbsStrings.list.noCustomers;

/**
 * "Does the operator have any customers yet?" for the operator VIEW only (the workspace and every
 * other principal never ask). `none` is true once the list has loaded and is empty: the operator
 * workspace is not a customer, so a fresh engine counts as having none.
 */
export function useCustomers() {
	const principal = usePrincipal();
	const admin = principal?.view === "admin";
	const q = useQuery({ ...activeCustomersQuery, enabled: admin });
	return {
		admin,
		loading: admin && q.isPending,
		none: admin && q.data != null && q.data.total === 0,
		items: q.data?.items ?? [],
	};
}

/** Path + search that open Customers with the "Add a customer" dialog. */
export const ADD_CUSTOMER = { to: "/admin/customers", search: { add: 1 } } as const;

/**
 * The knowledge-base pages of a fresh operator view: a knowledge base belongs to a customer, so
 * the way forward is "Add your first customer". The operator can also try the engine in their own
 * workspace with one click (a session sign-in; the admin key has no workspace to switch into).
 */
export function FirstCustomerPrompt() {
	const principal = usePrincipal();
	const switchScope = useSwitchScope();
	const canSwitch = operatorWorkspace(principal) !== null;
	return (
		<EmptyState
			title={t.title}
			body={
				<>
					{t.body}
					{canSwitch ? <span className="mt-2 block text-[13px] text-muted-foreground">{t.ownBody}</span> : null}
				</>
			}
			primaryAction={
				<Link {...ADD_CUSTOMER} className={buttonVariants({ variant: "default" })}>
					<UserPlus aria-hidden />
					{t.action}
				</Link>
			}
			secondaryAction={
				canSwitch ? (
					<Button variant="outline" onClick={() => void switchScope("workspace", "newKb")}>
						<Home aria-hidden />
						{t.own}
					</Button>
				) : undefined
			}
		/>
	);
}
