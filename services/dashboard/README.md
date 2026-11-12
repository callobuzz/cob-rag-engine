# cob-rag-engine dashboard

The dashboard (React 19, TypeScript, Vite, TanStack Router + Query, Tailwind v4, shadcn/ui),
served on http://127.0.0.1:8090 by the compose `dashboard` service. The previous dashboard,
`services/chat/`, runs on :8091 behind the `legacy-dashboard` profile for this release only.

## Setup

Node 22 LTS (`.nvmrc`) and pnpm (`corepack enable` gives the version pinned in `package.json`).

```bash
cd services/dashboard
pnpm install
pnpm dev            # http://127.0.0.1:5173 (127.0.0.1, not localhost: on Windows localhost is ::1)
```

The engine URL, scraper URL and ₹ rate come from `public/config.js` (`globalThis.RAG_CONFIG`).
The engine must allow this origin: add `http://127.0.0.1:5173` to `RAG_CORS_ORIGINS` in the
repo-root `.env` by hand (a list such as `["http://127.0.0.1:8090","http://127.0.0.1:5173"]`;
`make setup` keeps values you set yourself) and restart the app container. The scraper allows it
by default.

## Docker image

`Dockerfile`: Node 22 builds `dist/`, `nginx:1.27-alpine` serves it as the non-root `nginx`
user on port 8080 (compose publishes 8090). At start, `deploy/40-cob-config.sh` (in
`/docker-entrypoint.d/`) validates `RAG_DASHBOARD_ENGINE_URL`, `RAG_DASHBOARD_SCRAPER_URL` and
`RAG_DASHBOARD_USD_TO_INR`, writes `config.js` from `public/config.js.template` (same
`RAG_CONFIG` shape) and the nginx site from `deploy/nginx.conf.template`; a bad value stops the
container with a message naming the variable. The site has:

- a fallback to `index.html` for client routes (a reload on `/admin/customers?status=pending`
  works); missing `/assets/*` files are 404s;
- a Content-Security-Policy whose `connect-src` is `'self'` plus the engine and scraper
  origins, `script-src 'self'` (the theme script is the file `public/theme.js`), and a separate
  one for `/widget-demo.html` that may load `widget.js` from the engine and call only the
  engine (its loader is `public/widget-demo.js`, not inline);
- `nosniff`, `strict-origin-when-cross-origin`, a Permissions-Policy, `no-cache` for
  `index.html` and `config.js`, one-year `immutable` caching for hashed `/assets/*`, and
  `/healthz`.

```bash
docker compose up -d --build dashboard          # from the repo root
sh deploy/test-config.sh                        # start-script tests (POSIX sh)
E2E_IMAGE_URL=http://127.0.0.1:8090 E2E_ADMIN_KEY=… pnpm e2e --project image-smoke
```

## Scripts

| Script | What it does |
|---|---|
| `pnpm dev` / `pnpm preview` | Vite dev server (5173) / serve the production build (4173) |
| `pnpm build` | `tsc -b && vite build` into `dist/` |
| `pnpm typecheck` | TypeScript, strict, no emit |
| `pnpm lint` / `pnpm format` | Biome check / fix |
| `pnpm test` | Vitest unit and screen tests against MSW (no engine needed) |
| `pnpm e2e` | Playwright (starts `pnpm dev` if it is not running) |
| `pnpm gen:api` | Regenerate `src/lib/api/schema.d.ts` from `../../docs/openapi.json` |
| `pnpm gen:api:check` | Fail when `schema.d.ts` is stale (CI) |

## API types

`src/lib/api/schema.d.ts` is generated from `../../docs/openapi.json`; never edit it. Run
`pnpm gen:api` after the API changes; `pnpm gen:api:check` (CI) fails when it is stale. The
scraper's routes are typed by hand (`src/features/crawler/`), since it is a separate service.

## End-to-end tests

Playwright projects follow the dashboard README matrix: `superadmin-light-1440`,
`superadmin-dark-390`, `owner-light-390`, `owner-dark-1440`, `member-light-1440`, plus
`auth-flows` and `live-engine`.

- `e2e/shell.spec.ts`, `e2e/auth-flows.spec.ts` run against a stateful fake engine
  (`e2e/fixtures/mock-engine.ts`, served through `page.route`); nothing else is needed.
- `e2e/live.spec.ts` uses the running engine at `http://127.0.0.1:8000` (public branding, API-key
  sign-in). Set `E2E_ADMIN_KEY` to also sign in with the admin key, and `E2E_ENGINE_CORS=1` once
  `RAG_CORS_ORIGINS` lists the dev origin (otherwise the test adds CORS headers itself).
- `e2e/auth.spec.ts` runs the auth journeys against the real accounts stack. It needs the accounts
  epics merged, the dev e-mail log provider (messages land in `docker compose logs mailer`) and:
  `E2E_ACCOUNTS=1 E2E_ADMIN_KEY=… E2E_SUPERADMIN_EMAIL=… pnpm e2e`. Codes and links are read from
  `docker compose logs mailer app`.
- `E2E_SCREENSHOTS=<folder>` writes the 1440/390 screenshots there (outside the repo).
- `e2e/screenshots.spec.ts` (`@screenshots`) makes the README and docs images from the mock
  engine's Indian demo world: `DOCS_SCREENSHOTS=1 pnpm e2e --project docs-screenshots` writes
  `docs/assets/screenshots/dashboard-<screen>-<theme>-<width>.png` and the four README images.
- `e2e/smoke-image.spec.ts` checks the built image (headers, CSP, SPA fallback, `config.js`,
  `/healthz`, the widget preview) against a running stack; see "Docker image" above.
- The mock matrix also runs against the image: start it on the dev origin with the mock
  engine's addresses (`docker run --rm -d -p 127.0.0.1:5173:8080 -e
  RAG_DASHBOARD_ENGINE_URL=http://engine.mock -e RAG_DASHBOARD_SCRAPER_URL=http://scraper.mock
  cob-rag-dashboard:local`) and `pnpm e2e` uses it instead of starting `pnpm dev`.

## Layout

`src/routes/` file-based routes (`_app` = signed-in shell), `src/components/{ui,shell,auth,patterns}`,
`src/lib/` (api client and error map, auth store/refresh/guards, branding, format, strings, theme).
Conventions shared by every epic live in `docs/internal/plans/epics/dashboard/README.md`.

`src/features/assistant/` is the Assistant tab of a knowledge base and its preview console. Each
section form (`sections/`) validates with the zod schemas in `schemas.ts`, which mirror the
engine's `AssistantConfig` limit for limit, and saves through `useDraftSection`: the whole
configuration with the draft's `base_revision`, so a stale draft is a conflict, never an
overwrite. `preview/` reuses the playground's stream reader with the `assistant` request options.
To add a section, extend `ASSISTANT_SECTIONS` in `types.ts` and the body map in `AssistantTab.tsx`.

`src/features/assistant-setup/` is the guided setup (`/kbs/$kbId/setup?sid=`): the step follows the session's
`status`, so the URL resumes it. The website is crawled here, through the scraper (`crawl.ts`, review mode,
the job is deleted after the page text is read unless the owner ticks "Also add these pages"); the engine
never calls the scraper. Page text and model output are shown as plain text, never through `lib/markdown`.
The unit tests' questions fixture is a copy of the engine's `questions.json` (a test compares them).

## Forms and the shell

- **Every form** uses `useAppForm` + `useAppSubmit` (`src/lib/forms/app-form.ts`) and `SubmitButton`
  (`src/components/forms/`). Validation is `onTouched` then `onChange`: an error shows after the
  first blur and goes away the keystroke the input becomes valid. A server error on a field clears
  when that field is edited; a form-level alert clears when any field is edited or the form is
  submitted again. Submit buttons are never disabled for being invalid; a failed submit focuses the
  first invalid field. New-password forms use `NewPasswordFields` (live checklist, meter, repeat check).
- **The signed-in shell is one viewport tall** (`h-dvh`, `overflow-hidden`, a `minmax(0,1fr)` row):
  `#content` is the only scroll region, the document never scrolls, and the router resets `#content`
  on a new page (`scrollToTopSelectors`). `e2e/layout-scroll.spec.ts` guards it.
