---
route: /settings
audience: customer
title: "Settings"
topic: getting-started
summary: "How this dashboard looks and where it connects, saved in your browser, plus your workspace key."
starters:
  - "How do I switch to dark mode?"
  - "How do I show costs in rupees?"
  - "Where do I find my workspace API key?"
  - "What is the engine URL for?"
---
Settings changes how the dashboard looks and which engine it talks to. Appearance and display choices are saved in this browser only, so each person and each device keeps their own.

## What you see

- **Appearance**: **Theme** (**Light**, **Dark** or **System**) and **Table density** (**Comfortable** or **Compact**).
- **Display**: **Currency**, rupees or dollars. Costs are reported in US dollars; rupee amounts are an estimate at the display rate shown (for example ₹85 per $).
- **Connection**: the **Engine URL** and the **Crawler (scraper) URL**. They are set by the platform operator, so they are read only. Only an API-key sign-in can point somewhere else; it has a **Test** button that asks the engine for its health and a **Save engine URL** button.
- **Signed in as**: your e-mail or key, how you signed in (e-mail sign-in or API key) and **Log out everywhere else**.
- **Workspace**: the workspace name, its status, its plan with a link to Plan & billing, the number of knowledge bases against the limit, and the **Workspace API key** (`tk_`). The key is for your own apps and scripts and can do everything an owner can. **Create or rotate key** makes the first one or replaces it: the old one stops working at once. The new key is shown once.

## Common tasks

1. **Use dark mode.** Appearance, Theme, **Dark**.
2. **See amounts in rupees.** Display, Currency, **Rupees**.
3. **Get a workspace key.** Workspace section, **Create or rotate key**, confirm, copy the key. If you are signed in with this key you are signed out when you close the dialog.
4. **Test a different engine (API-key sign-in).** Enter the Engine URL, **Test**, then **Save engine URL**.

## Troubleshooting

- **"Can't reach the engine at ..."** (`network`): the engine is not running at that address, or it does not allow this dashboard's address.
- **Settings reset on another device**: they are saved per browser.
- **Cannot create or rotate the workspace key** (`owner_required`): only owners manage keys. Ask an owner.
- **`confirmation_required`** when deleting with a key: type the exact name shown.
- **"This key can't do that"** (`key_permission_denied`): the key has less permission than the action needs.

## Related

- [Profile & sessions](/docs/screens/profile)
- [Plan & billing](/docs/screens/plan)
- [Knowledge base settings](/docs/screens/kb-detail)
