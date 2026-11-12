# cob-rag-engine design system

How cob-rag-engine looks and sounds, as it is actually built. Every value below is copied from
the source and can be found with grep. Where this file and the source disagree, the source wins
and this file is the bug.

Sources:

| Surface | File | Tokens live in |
|---|---|---|
| Landing page | `docs/index.html` | `:root{}` in its `<style>` |
| Guide, deploy guide | `docs/guide.html`, `docs/deploy.html` | `:root{}` in each `<style>` |
| Benchmarks page | `docs/benchmarks.html` | its own shorter token names (`--display`, `--mono`); not yet moved to the `--color-*` names |
| Dashboard (the app) | `services/dashboard/src` | `styles/globals.css` (Tailwind v4 `@theme inline`) |
| Logo | `docs/assets/logo-mark.svg`, `services/dashboard/src/components/brand/` | `--logo-*` in `logo.css` |

One brand on every surface. The **public pages** and the **dashboard** share the palette and the
fonts, light and dark; the public pages default to light. Section 11 lists what still differs.

## 1. Principles

- Plain language first, then the exact command. A founder in Pune should follow it without a
  glossary.
- One brand on every surface: the public pages use the dashboard's palette and fonts. Light is the
  default; a visitor can switch to dark, and the choice is kept.
- Public pages share one stylesheet and one script (`docs/assets/site.css`, `docs/assets/theme.js`);
  a page adds only its own rules in a short `<style>`. No build step.
- Honest by default: a number on a page is a number we measured or can link to. Example data is
  labelled as example data, and it adds up.
- Motion explains, never decorates, and always has a reduced-motion answer. The landing page has none.

## 2. Public pages: colour

Tokens are `oklch()` in `docs/assets/site.css`: light in `:root`, dark in `:root[data-theme="dark"]`.
They are the dashboard's colours (section 7) converted to OKLCH. Every text pair is measured at
WCAG AA in both themes (lowest 5.2:1).

| Role | Token | Light | Dark |
|---|---|---|---|
| Background | `--color-paper` | white | `#07111d` |
| Alt band | `--color-paper-2` | `#f5f7fb` | `#0a1726` |
| Card / panel | `--color-panel`, `--color-panel-2` | `#f8fafd`, `#eef2f8` | `#0d1a2a`, `#122235` |
| Selected / tint | `--color-surface` | `#e1ebfa` | `#16304f` |
| Text | `--color-ink`, `--color-ink-2`, `--color-ink-3` | `#0b1726`, `#3a4a5e`, `#56667a` | `#eaf2fb`, `#a9bbcf`, `#8a9db3` |
| Border | `--color-rule`, `--color-rule-strong` | `#e2e8f0`, `#c3cedb` | `#1c2f46`, `#2c4664` |
| Primary (buttons) | `--color-primary` / `--color-primary-ink` | `#1a5fc4` / white | `#2f8cff` / `#04101f` |
| Links, focus | `--color-link`, `--color-focus` | `#1a5fc4` | `#6aaeff` |
| Status | `--color-green`, `--color-amber`, `--color-rose` | `#137636`, `#8a5300`, `#b3233a` | `#4ade9b`, `#f2b441`, `#ff7a85` |
| Code blocks | `--color-term`, `-term-ink`, `-term-comment`, `-term-key`, `-term-rule` | dark in both themes | |

Soft tints are `color-mix(in oklch, <colour> 7-11%, transparent)` (`--color-tip-a`, `--color-warn-a`,
`--color-danger-a`). The landing page's widget example uses `--color-mock*`: the example business's
own green, the same in both themes, because it is somebody else's brand.

## 3. Public pages: type

Google Fonts: `Schibsted Grotesk` 500/600/700 (display), `Red Hat Text` 400/500/600 (body) and
`JetBrains Mono` 400/600, the dashboard's three faces.

| Use | Size | Weight | Notes |
|---|---|---|---|
| Landing h1 | `clamp(2.5rem, 2.6vw + 1.25rem, 4rem)` | 600 | `letter-spacing: -.032em`, max 17ch |
| Page h1 | `clamp(2.25rem, 3vw + 1rem, 3.5rem)` | 600 | `max-width: 18ch`, `text-wrap: balance` |
| h2 | `clamp(1.75rem, 1.6vw + 1.2rem, 2.375rem)`; landing `clamp(1.875rem, 2.2vw + 1rem, 2.75rem)` | 600 | `letter-spacing: -.024em` |
| h3 | 19-24px | 600 | |
| Lede | 17-20px | 400 | `--color-ink-2`, max 64ch |
| Body | 16px | 400 | measure 70ch |
| Small / table | 14.5px | 400 | |
| Label / eyebrow | 12px mono | 600 | uppercase, `letter-spacing: .06em`, link colour |

Big display numbers use `font-variant-numeric: normal` (tabular figures open gaps around the point).

## 4. Public pages: spacing, shape, depth

- Spacing, an 8-pt base: `--space-2xs` 4px, `-xs` 8px, `-sm` 12px, `-md` 16px, `-lg` 24px,
  `-xl` 40px, `-2xl` 64px, `-3xl` 80px, `-4xl` 128px.
- Layout: `--page-max: 1200px`, `--gutter: clamp(16px, 4vw, 32px)`, nav `--nav-h: 64px`. Docs: a
  272px sticky contents rail and a prose column of at most 780px; the rail folds under 960px.
- Radii: `--radius-card` 14px, `--radius-sm` 10px, `--radius-control` 8px, `--radius-pill`.
- Depth: rules and bands first. One soft shadow (`--color-shadow`) on floating things only: the
  widget example and the dashboard screenshots.

## 5. Public pages: components

- **Nav** (every page, the same markup): sticky, frosted, brand left, five links centred
  (Overview, Get started, Guide, Deploy, Benchmarks; `aria-current` marks the page), then the theme
  switch (a 36px button, `aria-pressed`, moon or sun) and the GitHub button. Under 1100px the links
  become a scrolling second row.
- **Theme**: `assets/theme.js` runs in `<head>` before paint, reads `localStorage["cob.site.theme"]`
  (`dark`, else light) and sets `data-theme`; it also turns `no-js` into `js`.
- **Buttons** (`.btn`): 46px, 8px radius, 15px/600; primary is blue, ghost has a strong border.
- **Code blocks** (`.cb`): dark in both themes, a 44px bar with a label and a **Copy** button
  (Copy, then Copied or Failed for 2.2 s, announced politely).
- **Callouts**: `tip`, `warn`, `danger`, 3px left border plus a tint.
- **Tables**: a 14px-radius wrapper; under 700px rows become cards with `data-label` captions.
  The landing comparison scrolls sideways instead.
- **Tabs** (`[data-tabs]`): real `role="tab"` buttons, arrow keys, Home and End; without scripts
  every panel shows with its label.
- **Screenshots**: two `<picture>` elements per screen (light and dark; phone files under 700px);
  only the one matching the theme is displayed, so only it loads. They come from
  `services/dashboard/e2e/screenshots.spec.ts` (`DOCS_SCREENSHOTS=1 pnpm e2e --project docs-screenshots`).
- **Footer** (Ft1, mast-headed): "cob-rag-engine by Callobuzz" as plain text, the licence line with
  the commercial-licence link, and the page links.

## 6. Logo

- Source art `docs/assets/logo-mark.svg` (512x512, named groups `lm-frame`, `lm-block-top|mid|bottom`,
  `lm-channel-*`, `lm-merge`, `lm-sphere`). Raster sizes: `logo-32.png`, `logo-64.png`, `logo-180.png`
  (apple-touch), `logo-512.png` (social). The nav shows it at 28px.
- Palette: the `--logo-*` custom properties, for example `--logo-cyan: oklch(77.3% 0.132 219.9)`,
  `--logo-azure: oklch(57.5% 0.19 257.8)`, `--logo-ice: oklch(93.8% 0.057 208.6)`.
- Animation is state-driven by `data-state`: `armed` (held) -> `assemble` (plays once, the frame
  draws, blocks slide in and lock, the sphere ignites; the dashboard's `ASSEMBLE_MS` is 3100) ->
  `live` (a calm loop: breathing, a glint, a pulse along the channels). It pauses off-screen and
  when the tab is hidden (`.is-paused`), and uses transform and opacity only, no SVG filters.
- Reduced motion: the logo is the finished static mark; frame dashes are fully drawn, pulses and
  flares are hidden.

## 7. Dashboard (app) tokens

Tailwind v4 with shadcn (`components.json`: style `new-york`, base colour `slate`, icons `lucide`).
Raw tokens live in `:root` (light) and `.dark` (dark) in `styles/globals.css`, and `@theme inline`
maps them to utilities (`bg-card`, `text-muted-foreground`, ...). Both themes are designed, not
inverted. `src/lib/branding/contrast.test.ts` checks every text and background pair for WCAG AA
(4.5:1) in both.

| Role | Token | Light | Dark |
|---|---|---|---|
| Background | `--background` | `#f5f7fb` | `#07111d` |
| Muted band | `--muted` | `#eef2f8` | `#0a1726` |
| Surface | `--card`, `--popover` | `#ffffff` | `#0d1a2a` |
| Secondary | `--secondary` | `#f4f7fb` | `#122235` |
| Hover | `--accent` | `#edf2f9` | `#16293f` |
| Border | `--border` | `#e2e8f0` | `#1c2f46` |
| Input border | `--input` | `#cbd5e1` | `#2a4460` |
| Text | `--foreground` | `#0b1726` | `#eaf2fb` |
| Text 2 | `--foreground-2` | `#3a4a5e` | `#a9bbcf` |
| Muted text | `--muted-foreground` | `#5b6b7f` | `#8093a9` |
| Accent / primary | `--primary`, `--ring` | `#1a5fc4` | `#2f8cff` |
| Text on primary | `--primary-foreground` | `#ffffff` | `#04101f` |
| Info | `--info` | `#0e7490` | `#3fd8f5` |
| Success | `--success` | `#137636` | `#4ade9b` |
| Warning | `--warning` | `#955a06` | `#f2b441` |
| Danger | `--destructive` | `#b8243a` | `#ff7a85` |
| Violet | `--violet` | `#6d28d9` | `#a78bfa` |

Each status colour has a `-soft` tint for badge backgrounds (light `rgba(..., 0.08-0.1)`, dark
`rgba(..., 0.13)`). Charts use `--chart-1` to `--chart-5` (blue, cyan, violet in both themes).
The **operator brand** (`--brand`, default `#2f8cff`, plus `--brand-foreground`, `--brand-text-light`
`#1a5fc4`, `--brand-text-dark` `#5aa5ff`) is overwritten at runtime from `GET /api/v1/public/branding`;
`bg-brand` and `text-brand-text` are for the operator's own colour, `primary` is the product's.

- Fonts (via `@fontsource`): display `"Schibsted Grotesk"` 500-800, body `"Red Hat Text"` 400-600,
  mono `"JetBrains Mono"` 400/500. Stacks end in `"Segoe UI", system-ui, sans-serif` / `Consolas, monospace`.
- Type scale: body `14px / 1.5`; headings use the display font at `letter-spacing: -0.01em`;
  `code` 12.5px; buttons 13.5px/600 (small 12.5px); badges 11.5px/600; `.kbd` 11px; `.nav-label`
  mono 10.5px uppercase at `0.1em`.
- Radii: `--radius: 10px` (cards, `rounded-md`), `--radius-sm-token: 7px` (buttons and inputs,
  `rounded-sm`), `--radius-lg-token: 14px` (dialogs). Badges are full pills.
- Depth: `--shadow` is `0 10px 30px -14px rgba(15, 23, 42, 0.25), 0 0 0 1px rgba(15, 23, 42, 0.04)`
  in light and `0 12px 40px -12px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.03)` in dark,
  used for popovers (`--shadow-pop`). Cards use shadcn's `shadow-sm`.
- Layout: content `max-w-[1280px]`, padding 16px, 28px from 861px; sidebar drawer `272px`.
- Density: `html[data-density="compact"]` trims `.density-cell` padding from `10px 16px` to `6px`
  vertical. Stored in `localStorage["cob.density"]`.
- Theme: `public/theme.js` runs before first paint, reads `localStorage["cob.theme"]`
  (`light` | `dark` | `system`, default `system`) and toggles the `dark` class. It is a file, not an
  inline script, so the Content-Security-Policy needs no `'unsafe-inline'`.

## 8. Dashboard components (shadcn conventions)

Components are shadcn copies in `components/ui/`, restyled with tokens ("token fix" comments),
variants through `cva`, class merging through `cn`, and `data-slot` attributes.

- **Button**: variants `default` (primary), `brand`, `destructive` (outlined rose, tinted on hover),
  `outline` (the default variant), `secondary`, `ghost`, `link`. Sizes `default` h-9, `sm` 30px,
  `lg` 42px, `icon` 36px, `icon-sm` 32px. Hover on filled buttons is `brightness-110`.
- **Card**: `rounded-xl border bg-card py-6 shadow-sm`, 24px gaps; `CardTitle` is 600, `CardDescription`
  is `text-sm text-muted-foreground`.
- **Input**: 38px, `bg-muted`, `border-input`; focus is a primary border plus a
  `0 0 0 3px var(--primary-soft)` halo; `aria-invalid` turns the border to `--destructive`.
- **Badge**: 22px pill; tones `ok`, `warn`, `bad`, `info`, `default`, `outline`.
  `StatusBadge` always pairs an icon with text, never colour alone.
- **Table**: shadcn table in an `overflow-x-auto` wrapper, 14px, row hover `bg-muted/50`. Under
  600px `.stack-sm` turns rows into cards.
- **Copy**: `CopyButton` copies, swaps to a check icon and "Copied" for 2 s, and announces through a
  polite `aria-live` region. `SecretReveal` shows a secret once.
- **Other patterns** in `components/patterns/`: `ConfirmDialog` and `TypedConfirmDialog` (type the
  name to confirm a destructive action), `EmptyState`, `ErrorState`, `Skeletons`, `Segmented`,
  `UsageMeter`, `DataTable`, `Pager`.
- **Powered by**: `PoweredBy` renders "cob-rag-engine by Callobuzz" on every public page and at the
  bottom of every signed-in page. It has no prop to hide or restyle it; the text and URL come from a
  constant, not a setting.

## 9. Motion

Public pages:

- Easing `--ease-out: cubic-bezier(0.16, 1, 0.3, 1)`; also `--ease-in: cubic-bezier(0.7, 0, 0.84, 0)`
  and `--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1)` (landing).
- Durations: `--dur-micro: 120ms` for hover, colour, press; `--dur-short: 220ms`; `--dur-long: 520ms`.
- Reveal keyframes on the landing page: `hm-in` (fade up 10px) and `hm-mask`. Diagrams animate
  with transform, opacity, dash offsets only.
- The guide has no scroll animation; smooth scrolling and the contents highlight are the only motion.

Dashboard: `--t: 160ms`, `--ease: cubic-bezier(0.2, 0.8, 0.2, 1)`; `shake` (0.36s, a failed form
field), `blink` (the streaming caret, `steps(2)`), and `tw-animate-css` for dialogs and sheets.

Reduced motion, all surfaces: under `prefers-reduced-motion: reduce`, `scroll-behavior` is `auto` and
`animation` and `transition` are `none !important`. Information that motion carried must also exist
without it (the landing diagrams fall back to static dashes; the logo shows its finished state;
the typing caret is removed). Anything that loops pauses when off-screen.

## 10. Accessibility

- Contrast: public text pairs are verified (comments in each page's head: "contrast: pass");
  `--color-ink-3` is 5.9:1 on paper; the dashboard is tested at 4.5:1 for every pair and tint
  in both themes. Do not place muted text on a tint without checking.
- Focus: `:focus-visible` is `2px solid var(--color-focus)` with a 3px offset (public pages) and
  `2px solid var(--ring)` with a 2px offset (dashboard). Never remove an outline without
  replacing it. Programmatic focus targets (`tabindex="-1"`) show no ring.
- Skip link: every public page starts with `.skip`, hidden at `top: -80px` until focused.
- Targets: buttons 40-46px on public pages; the nav and choice chips are at least 40px tall.
- Colour is never the only signal: badges carry icons and words, callouts carry a label.
- Code blocks are keyboard-scrollable; copy results are announced in a polite live region.
- Landmarks: `nav[aria-label="Main"]`, one `main`, `aria-label="On this page"` on the contents.
- Print (guide): a light palette, no chrome, every adaptive variant shown with an "Applies when" tag.
- Layout: no horizontal page scroll at 390px; `html{overflow-x: clip}`.
- Language: `lang="en"`.

## 11. Public pages vs app (what still differs)

| | Public pages (`docs/`) | Dashboard |
|---|---|---|
| Theme | light by default, dark through `data-theme`, the choice kept | light and dark through `.dark`; follows the system |
| Colour model | `oklch()` (the dashboard's colours, converted) | hex and `rgba()` |
| Body size | 16px | 14px |
| Card radius | 14px | 10px, 14px dialogs |
| Button height | 46px (40px small) | 36px (30px small) |
| Token names | `--color-paper`, `--color-ink`, `--color-primary` | shadcn: `--background`, `--foreground`, `--primary` |
| Build | hand-written HTML, `assets/site.css` | Vite, React, Tailwind v4, shadcn |

## 12. Voice and wording

- Plain language. Short sentences. Say what to do, then why. Prefer "turn on" to "enable",
  "start" to "initialise", "your server" to "the host".
- **Knowledge base**, never "corpus", in anything a person reads (pages, dashboard, e-mails).
  `corpus` stays in code, API paths and field names, where it must match the API; when a page quotes
  one, say what it means.
- **Customer** for the business you serve; "tenant" only when naming the API.
- Examples are Indian by default: domains like `chat.yourbusiness.in`, money in rupees
  (`₹999`, `₹1,499`, Indian digit grouping), phone numbers as `+91 20 2545 1234`, places like Pune,
  Kothrud, Baner, Aundh, and a pathology lab ("Shree Labs") as the standing example business.
  Never US-specific: no dollars-first pricing, no US phone formats, no US-only providers or
  holidays. Dollars appear only where the provider bills in dollars, and the dashboard shows
  a labelled rupee estimate (`RAG_DASHBOARD_USD_TO_INR`).
- Dates read "2 October 2026".
- Do not claim what was not measured. Link the result, or leave the number out.
- Commands: one command per copy block where possible; comments in a block explain, they do not
  repeat. Name the setting exactly (`RAG_ENV=prod`).
- Buttons are verbs ("Copy", "Start at step 1"). Errors say what happened and what to do next.
- No exclamation marks, no emoji, no "simply" or "just".

## 13. Attribution

Every public page and every signed-in dashboard page carries **"cob-rag-engine by Callobuzz"**
as plain text, never a link. On documentation pages it is the first
item in the footer (14px, bold, `--color-ink`) followed by "© Callobuzz"; `og:site_name` is the
same string. In the dashboard it is `PoweredBy`, 12.5px muted. It is shown by default; the operator can hide it in Branding (`hide_powered_by`), which hides it in the dashboard, the e-mails and the widget. In e-mail it
is the last line of every template (`POWERED_BY`), and the widget panel footer shows
"cob-rag-engine by Callobuzz" as plain text. Never restyle it into the background.

## 14. Checklist for a new page

1. Link `assets/site.css`, load `assets/theme.js` in `<head>`, and use the shared font link; add only
   page rules, built from the tokens, never a new hex value.
2. `<html class="no-js">`, `<meta name="theme-color" content="#ffffff">`.
3. Skip link, the shared nav and footer (copy them from `docs/guide.html`), one `h1`.
4. Code in `.cb` blocks with a labelled bar and a Copy button; callouts only `tip`, `warn`, `danger`.
5. Check both themes and 390px wide: no horizontal scroll, contrast AA.
6. Indian example, "knowledge base", no unmeasured claims; example data says so and adds up.

## 15. Landing site

The deployer's public website (`services/dashboard/landing/`, rendered by `src/callobuzz_rag/landing/`) is
the one public surface with a colour the deployer chooses (light and dark, like every surface). It does not use the
dashboard's tokens or the public pages' blue.

- **Colour:** one brand colour (and an optional accent) becomes the whole palette in `landing/theme.py`:
  `bg`, `bg-2`, `surface`, `ink`, `ink-2`, `ink-3`, `rule`, `brand`, `brand-ink`, `brand-text`,
  `brand-soft`, `brand-1` to `brand-12`, `accent`, `accent-text`, `focus`, `glow-a`, `glow-b`, `scrim`, in
  a light and a dark set. Every text pair is measured and the engine darkens or lightens the brand until it
  reads; the editor shows the report and says where it adjusted. Glow and grain never sit under text
  without the scrim.
- **Type:** six self-hosted pairings (`landing/fonts.py`): Neutral, Engineered, Editorial, Warm, Bold and
  House, each with a Devanagari face and a metric-matched fallback. The headline is the largest
  contentful paint, never an image. Hindi text uses the pairing's Devanagari face by `unicode-range`.
- **Motion:** `--ease-out`, `--ease-in-out`, `--dur-micro` (120 ms), `--dur-short` (220 ms) and
  `--dur-reveal` (600 ms) in `landing/styles/base.css`. Content is visible without scripts; with
  `prefers-reduced-motion` the story is static. No scroll hijacking.
- **Budgets:** at most 15 KB of script on the home page and 25 KB of styles (gzipped), at most two preloaded
  font files, no image required above the fold (`tests/unit/test_landing_budgets.py`).
- **The editor** (`/admin/landing`) is an ordinary dashboard page (sections 7 and 8): shadcn parts, the
  dashboard's tokens, plain sentences. It shows the website's own colours only inside the preview frames.
