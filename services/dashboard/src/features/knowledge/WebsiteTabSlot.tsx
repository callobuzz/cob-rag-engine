import { WebsiteTab } from "@/features/website/WebsiteTab";

/** Where the Knowledge page mounts BIZ1's "From your website" tab. */
export function WebsiteTabSlot({ kbId }: { kbId: string }) {
	return <WebsiteTab kbId={kbId} />;
}
