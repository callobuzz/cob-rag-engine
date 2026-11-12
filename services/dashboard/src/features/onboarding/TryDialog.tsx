import { useQuery } from "@tanstack/react-query";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { suggestionsQuery } from "./api";
import { TryChat } from "./TryChat";

/** "Try your assistant" from Home: the same chat as step 3, in a dialog, in English suggestions. */
export function TryDialog({
	kb,
	name,
	avatarUrl,
	open,
	onOpenChange,
}: {
	kb: string;
	name: string | null;
	avatarUrl: string | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const suggestions = useQuery({ ...suggestionsQuery(kb, "en"), enabled: open });
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[560px]">
				<DialogHeader>
					<DialogTitle>{onboardingStrings.home.tryDialogTitle}</DialogTitle>
					<DialogDescription>{onboardingStrings.three.lead}</DialogDescription>
				</DialogHeader>
				<TryChat kb={kb} name={name} avatarUrl={avatarUrl} suggestions={suggestions.data?.items.map((i) => i.text)} />
			</DialogContent>
		</Dialog>
	);
}
