import { Link } from "@tanstack/react-router";
import { Lock, SearchX } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { en } from "@/lib/strings/en";

/** "Not available for your account" (wrong view: no redirect loop, no data request). */
export function NotAvailable() {
	useDocumentTitle(en.common.notAvailableTitle);
	return (
		<Message icon="lock" title={en.common.notAvailableTitle} body={en.common.notAvailableBody} testId="not-available" />
	);
}

/** Not found, inside the shell. */
export function NotFoundPage() {
	useDocumentTitle(en.common.notFoundTitle);
	return <Message icon="search" title={en.common.notFoundTitle} body={en.common.notFoundBody} testId="not-found" />;
}

/** Route notFoundComponent: `requireView` throws notFound({data: {reason: "view"}}). */
export function RouteNotFound({ data }: { data?: unknown }) {
	const reason = (data as { reason?: string } | undefined)?.reason;
	return reason === "view" ? <NotAvailable /> : <NotFoundPage />;
}

function Message({
	icon,
	title,
	body,
	testId,
}: {
	icon: "lock" | "search";
	title: string;
	body: string;
	testId: string;
}) {
	const Icon = icon === "lock" ? Lock : SearchX;
	return (
		<section
			data-testid={testId}
			className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<Icon className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{title}
			</h1>
			<p className="m-0 max-w-[560px] text-foreground-2">{body}</p>
			<Link to="/" className={buttonVariants({ variant: "outline" })}>
				{en.common.goHome}
			</Link>
		</section>
	);
}
