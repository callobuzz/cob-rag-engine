import { MessageCircleQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { openCob } from "@/lib/cob-bus";
import { docsStrings as t } from "@/lib/strings/docs";
import { fill } from "@/lib/strings/en";

/**
 * "Ask COB about this page": opens COB's panel with the question in its input (not sent). Without
 * the panel listening (COB switched off, a key sign-in) the bus has no listener and nothing happens.
 */
export function AskCobButton({ slug, title }: { slug: string; title: string }) {
	return (
		<Button
			type="button"
			variant="outline"
			size="sm"
			onClick={() => openCob({ docsSlug: slug, prompt: fill(t.askCobPrompt, { title }), send: false })}
		>
			<MessageCircleQuestion aria-hidden />
			{t.askCob}
		</Button>
	);
}
