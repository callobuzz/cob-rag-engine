import { useState } from "react";
import { toast } from "sonner";
import type { AssistantState } from "@/features/assistant/types";
import { avatarSrc } from "@/lib/avatar";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { AvatarUpload } from "./AvatarUpload";
import { usePatchAvatar } from "./api";
import { avatarProblem } from "./WebsiteStep";

/** The address in an assistant version's identity (the draft when there is one, else the live one). */
export function currentAvatar(state: Pick<AssistantState, "draft" | "published">): string | null {
	return (state.draft ?? state.published)?.config.identity.avatar_url ?? null;
}

/**
 * The picture on the simple Assistant page: choose a file and it is uploaded and put in the
 * assistant's draft, which the owner publishes with the page's usual Publish bar.
 */
export function AssistantAvatar({ kb, state }: { kb: string; state: Pick<AssistantState, "draft" | "published"> }) {
	const patch = usePatchAvatar(kb);
	const [error, setError] = useState<string | null>(null);
	return (
		<AvatarUpload
			current={avatarSrc(currentAvatar(state))}
			busy={patch.isPending}
			error={error}
			onPick={(file) => {
				setError(null);
				patch.mutate(file, {
					onSuccess: () => toast.success(onboardingStrings.avatarPage.saved),
					onError: (e) => setError(avatarProblem(e)),
				});
			}}
		/>
	);
}
