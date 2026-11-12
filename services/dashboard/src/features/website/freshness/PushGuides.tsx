import { CopyButton } from "@/components/patterns/CopyButton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { websiteStrings } from "@/lib/strings/website";

const t = websiteStrings.guides;

function Code({ children, label }: { children: string; label: string }) {
	return (
		<div className="grid gap-1.5">
			<section
				aria-label={label}
				// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable code box must be reachable by keyboard
				tabIndex={0}
				className="overflow-x-auto rounded-md border border-border bg-muted"
			>
				<pre className="m-0 p-3 text-[12.5px]">
					<code>{children}</code>
				</pre>
			</section>
			<div>
				<CopyButton value={children} ariaLabel={`Copy ${label}`} />
			</div>
		</div>
	);
}

/**
 * Per-platform steps for the update link. `pushUrl` is the real link right after it was made,
 * else a placeholder to replace. Plain words first, the code second.
 */
export function PushGuides({ pushUrl }: { pushUrl?: string }) {
	const link = pushUrl ?? t.placeholder;
	const wordpress = `add_action('transition_post_status', function ($new, $old, $post) {
    if ($new === 'publish') {
        wp_remote_post('${link}', ['headers' => ['Content-Type' => 'application/json'],
            'body' => wp_json_encode(['url' => get_permalink($post)]), 'blocking' => false]);
    }
}, 10, 3);`;
	const curl = `curl -X POST "${link}" -H "Content-Type: application/json" -d '{"url": "https://shreedental.in/offers"}'`;
	return (
		<div className="grid gap-3" data-testid="push-guides">
			<h3 className="m-0 text-[14px] font-semibold">{t.title}</h3>
			{pushUrl ? null : <p className="m-0 text-[13px] text-muted-foreground">{t.needLink}</p>}
			<Tabs defaultValue="wordpress">
				<TabsList>
					<TabsTrigger value="wordpress">{t.tabs.wordpress}</TabsTrigger>
					<TabsTrigger value="any">{t.tabs.any}</TabsTrigger>
					<TabsTrigger value="indexnow">{t.tabs.indexnow}</TabsTrigger>
				</TabsList>
				<TabsContent value="wordpress" className="grid gap-3 pt-3 text-[13.5px]">
					<p className="m-0">{t.wordpress.intro}</p>
					<div className="grid gap-1">
						<strong>{t.wordpress.pluginTitle}</strong>
						<p className="m-0">{t.wordpress.plugin}</p>
					</div>
					<div className="grid gap-1.5">
						<strong>{t.wordpress.codeTitle}</strong>
						<Code label="the WordPress code">{wordpress}</Code>
					</div>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.wordpress.note}</p>
				</TabsContent>
				<TabsContent value="any" className="grid gap-3 pt-3 text-[13.5px]">
					<p className="m-0">{t.any.intro}</p>
					<Code label="the command">{curl}</Code>
					<p className="m-0">{t.any.getTitle}</p>
					<Code label="the address">{`${link}?url=https://shreedental.in/offers`}</Code>
				</TabsContent>
				<TabsContent value="indexnow" className="grid gap-3 pt-3 text-[13.5px]">
					<p className="m-0">{t.indexnow.intro}</p>
					<Code label="the update link">{link}</Code>
				</TabsContent>
			</Tabs>
		</div>
	);
}
