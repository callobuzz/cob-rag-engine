import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "@tanstack/react-router";
import { CircleHelp } from "lucide-react";
import { usePrincipal } from "@/lib/auth/store";
import { docsStrings as t } from "@/lib/strings/docs";
import { docsTreeQuery, pageForRoute } from "./api";

/**
 * The "?" beside a screen's title: a link to that screen's help page in the Docs. It looks the
 * page up by the screen's route in the Docs tree, so no screen needs any wiring; a screen with
 * no help page for this role shows nothing. Only people (sessions) have the Docs.
 */
export function HelpLink() {
	const principal = usePrincipal();
	const pathname = useLocation({ select: (l) => l.pathname });
	const tree = useQuery({ ...docsTreeQuery, enabled: principal?.auth === "session" });
	const page = pageForRoute(tree.data, pathname);
	if (!page) return null;
	return (
		<Link
			to="/docs/$"
			params={{ _splat: page.slug }}
			aria-label={t.helpLink}
			title={t.helpLink}
			className="grid size-7 shrink-0 place-items-center rounded-full text-muted-foreground no-underline hover:bg-accent hover:text-foreground"
		>
			<CircleHelp className="size-[18px]" aria-hidden />
		</Link>
	);
}
