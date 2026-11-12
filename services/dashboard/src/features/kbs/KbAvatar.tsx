import type { Corpus } from "@/lib/api/queries";
import { initials } from "@/lib/auth/principal";
import { kbColor } from "@/lib/kb-selection";

/** Mockup `.kb-avatar`: initials on one of eight AA-safe colours picked from the id. */
export function KbAvatar({ kb, size = 24 }: { kb: Pick<Corpus, "id" | "name">; size?: number }) {
	return (
		<span
			aria-hidden
			style={{
				background: kbColor(kb.id),
				width: size,
				height: size,
				fontSize: size >= 36 ? 15 : 11,
				borderRadius: size >= 36 ? 10 : 6,
			}}
			className="grid shrink-0 place-items-center font-bold text-white"
		>
			{initials(kb.name)}
		</span>
	);
}
