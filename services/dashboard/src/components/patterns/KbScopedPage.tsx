import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database } from "lucide-react";
import type { ReactNode } from "react";
import { useSelectedKb } from "@/components/shell/KbSwitcher";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { FirstCustomerPrompt, useCustomers } from "@/features/customers/first-customer";
import { type Corpus, corporaSwitcherQuery } from "@/lib/api/queries";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { kbsStrings } from "@/lib/strings/kbs";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { CardSkeleton } from "./Skeletons";

/**
 * A KB-scoped page (README "Knowledge-base selection"): `?kb=`, else the remembered one, else the
 * first by name, else the "Create your first knowledge base" empty state. Renders `children(kb)`
 * once a knowledge base is chosen.
 */
export function KbScopedPage({
	title,
	emptyBody,
	children,
}: {
	title: string;
	/** What this page needs a knowledge base for (defaults to the documents wording). */
	emptyBody?: string;
	children: (kb: Corpus) => ReactNode;
}) {
	const q = useQuery(corporaSwitcherQuery);
	const { selected } = useSelectedKb();
	const customers = useCustomers();
	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={1} />
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (q.isError) {
		return (
			<>
				<PageHeader title={title} />
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</>
		);
	}
	if (!selected) {
		// The operator view with no customers yet: a knowledge base needs one, so start there.
		if (customers.loading) return <CardSkeleton lines={2} />;
		if (customers.none) {
			return (
				<>
					<PageHeader title={title} />
					<FirstCustomerPrompt />
				</>
			);
		}
		// One customer and no knowledge base anywhere: name them and prefill the picker.
		const only = customers.items.length === 1 ? customers.items[0] : undefined;
		return (
			<>
				<PageHeader title={title} />
				<EmptyState
					title={only ? fill(kbsStrings.list.createFor, { customer: only.name }) : t.common.noKbTitle}
					body={emptyBody ?? t.common.noKbBody}
					primaryAction={
						<Link
							to="/kbs"
							search={only ? { tenant: only.id, new: 1 } : undefined}
							className={buttonVariants({ variant: "default" })}
						>
							<Database aria-hidden />
							{only ? fill(kbsStrings.list.createFor, { customer: only.name }) : t.common.noKbAction}
						</Link>
					}
				/>
			</>
		);
	}
	return <>{children(selected)}</>;
}
