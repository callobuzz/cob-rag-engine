import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, GripVertical } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { cn } from "@/lib/utils";
import { useDraft } from "./DraftProvider";
import { canMove, canSwitchOff, fieldId, moveSection, moveSectionTo, type Section } from "./draft";
import { Fields, type Rec } from "./fields";
import { GenerateDialog } from "./GenerateDialog";
import { PresetDialog } from "./PresetDialog";
import { INTRO_FIELDS, SECTION_FIELDS, SECTION_TITLES } from "./sections";

function titleOf(section: Section): string {
	return SECTION_TITLES[section.type]?.title ?? section.type;
}

function SectionCard({
	section,
	index,
	open,
	onToggleOpen,
	onMove,
	onDragStart,
	onDropOn,
}: {
	section: Section;
	index: number;
	open: boolean;
	onToggleOpen: () => void;
	onMove: (delta: -1 | 1) => void;
	onDragStart: () => void;
	onDropOn: () => void;
}) {
	const draft = useDraft();
	const title = titleOf(section);
	const required = !canSwitchOff(section);
	const rec = section as unknown as Rec;
	const specs = SECTION_FIELDS[section.type] ?? [];
	const introHasContent = INTRO_FIELDS.some((f) => rec[f.key] != null);
	const introFocus = INTRO_FIELDS.some((f) => draft.pendingFocus === fieldId(`home.${section.id}.${f.key}`));
	const replace = (next: Rec) =>
		draft.update((d) => {
			d.home[index] = next as unknown as Section;
		});
	const off = section.enabled === false;

	return (
		<li
			data-testid={`section-${section.id}`}
			data-enabled={!off}
			onDragOver={(e) => e.preventDefault()}
			onDrop={onDropOn}
			className={cn("rounded-lg border border-border bg-card", off && "bg-secondary/50")}
		>
			<div className="flex flex-wrap items-center gap-2 p-3">
				{required ? (
					<span className="size-5" aria-hidden />
				) : (
					<button
						type="button"
						draggable
						onDragStart={onDragStart}
						title={fill(t.sections.drag, { title })}
						aria-label={fill(t.sections.drag, { title })}
						className="cursor-grab text-muted-foreground"
					>
						<GripVertical className="size-5" aria-hidden />
					</button>
				)}
				<button
					type="button"
					aria-expanded={open}
					aria-label={fill(open ? t.sections.collapse : t.sections.expand, { title })}
					onClick={onToggleOpen}
					className="flex min-w-0 flex-1 items-center gap-2 text-left"
				>
					{open ? (
						<ChevronDown className="size-4 shrink-0" aria-hidden />
					) : (
						<ChevronRight className="size-4 shrink-0" aria-hidden />
					)}
					<span className="grid min-w-0">
						<span className={cn("truncate text-[14px] font-semibold", off && "text-muted-foreground")}>{title}</span>
						<span className="truncate text-[12.5px] text-muted-foreground">{SECTION_TITLES[section.type]?.about}</span>
					</span>
				</button>
				<div className="flex items-center gap-1">
					<Button
						type="button"
						size="icon-sm"
						variant="ghost"
						disabled={!canMove(draft.content.home, section.id, -1)}
						aria-label={fill(t.sections.moveUp, { title })}
						onClick={() => onMove(-1)}
					>
						<ArrowUp aria-hidden />
					</Button>
					<Button
						type="button"
						size="icon-sm"
						variant="ghost"
						disabled={!canMove(draft.content.home, section.id, 1)}
						aria-label={fill(t.sections.moveDown, { title })}
						onClick={() => onMove(1)}
					>
						<ArrowDown aria-hidden />
					</Button>
					{required ? (
						<span className="px-2 text-[12.5px] text-muted-foreground">{t.sections.alwaysOn}</span>
					) : (
						<div className="flex items-center gap-2 px-1">
							<Switch
								checked={!off}
								aria-label={fill(t.sections.switchLabel, { title })}
								onCheckedChange={(v) =>
									draft.update((d) => {
										const target = d.home[index];
										if (target) target.enabled = v;
									})
								}
							/>
							<span className="w-12 text-[12.5px] text-muted-foreground">{off ? t.sections.off : t.sections.on}</span>
						</div>
					)}
				</div>
			</div>
			{open ? (
				<div className="grid gap-5 border-t border-border p-4">
					<Fields specs={specs} value={rec} onChange={replace} path={`home.${section.id}`} limitBase={section.type} />
					<details className="rounded-sm border border-border p-3" open={introHasContent || introFocus}>
						<summary className="cursor-pointer text-[13px] font-semibold">{t.sections.intro}</summary>
						<p className="mt-1 mb-3 text-[12.5px] text-muted-foreground">{t.sections.introAbout}</p>
						<Fields
							specs={INTRO_FIELDS}
							value={rec}
							onChange={replace}
							path={`home.${section.id}`}
							limitBase={section.type}
						/>
					</details>
				</div>
			) : null}
		</li>
	);
}

/** The home page as a list of sections: switch each on or off, reorder, and edit its texts. */
export function HomeSectionsEditor() {
	const draft = useDraft();
	const home = draft.content.home;
	const [open, setOpen] = useState<Set<string>>(() => new Set(draft.openSection ? [draft.openSection] : []));
	const [dragging, setDragging] = useState<string | null>(null);
	const [announce, setAnnounce] = useState("");

	useEffect(() => {
		if (draft.openSection) setOpen((prev) => new Set(prev).add(draft.openSection as string));
	}, [draft.openSection]);

	const say = (list: Section[], id: string) => {
		const section = list.find((s) => s.id === id);
		if (section) {
			setAnnounce(
				fill(t.sections.moved, { title: titleOf(section), n: list.indexOf(section) + 1, total: list.length }),
			);
		}
	};
	const move = (id: string, delta: -1 | 1) => {
		const next = moveSection(home, id, delta);
		if (next === home) return;
		draft.update((d) => {
			d.home = next;
		});
		say(next, id);
	};
	const dropOn = (targetId: string) => {
		if (!dragging) return;
		const next = moveSectionTo(
			home,
			dragging,
			home.findIndex((s) => s.id === targetId),
		);
		setDragging(null);
		if (next === home) return;
		draft.update((d) => {
			d.home = next;
		});
		say(next, dragging);
	};

	return (
		<div className="grid gap-4" data-testid="home-sections">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<div className="grid gap-0.5">
					<h2 className="m-0 text-[16px] font-bold">{t.sections.heading}</h2>
					<p className="m-0 max-w-[60ch] text-[13px] text-muted-foreground">{t.sections.lead}</p>
				</div>
				<div className="flex flex-wrap gap-2">
					<PresetDialog />
					<GenerateDialog />
				</div>
			</div>
			<p role="status" aria-live="polite" className="sr-only" data-testid="sections-live">
				{announce}
			</p>
			<ol className="m-0 grid list-none gap-2 p-0">
				{home.map((section, i) => (
					<SectionCard
						key={section.id}
						section={section}
						index={i}
						open={open.has(section.id)}
						onToggleOpen={() =>
							setOpen((prev) => {
								const next = new Set(prev);
								if (!next.delete(section.id)) next.add(section.id);
								return next;
							})
						}
						onMove={(delta) => move(section.id, delta)}
						onDragStart={() => setDragging(section.id)}
						onDropOn={() => dropOn(section.id)}
					/>
				))}
			</ol>
		</div>
	);
}
