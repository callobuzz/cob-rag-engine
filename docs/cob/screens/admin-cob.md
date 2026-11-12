---
route: /admin/cob
audience: admin
title: "COB (pages COB reads)"
topic: operations
summary: "The engine's built-in COB pages, and the pages you add for COB to answer from."
starters:
  - "How do I add my support hours so COB can answer with them?"
  - "What happens to my own pages when I upgrade the engine?"
  - "How do I stop customers seeing a page?"
---
COB answers from two sets of pages. This screen, for the platform operator only, shows both. Customers and team members cannot add, change or delete anything here.

## What you see

- **Built-in docs**: the pages that come with the engine. You see the docs version COB has read, whether it matches the version shipped with this engine, the last sync, the state of COB's self-check, and a **Resync now** button. The list below is read-only. These pages update themselves after every upgrade.
- **Platform docs**: pages you add yourself. Each one shows who can see it and when it was added.

## Common tasks

1. **Add a page from text.** Open **Platform docs**, choose who can see it, write a title and the text, and choose **Add page**. COB can use it within a minute.
2. **Add a file.** Choose **Upload a file** (a document, PDF or text file). It is added for the people chosen next to the button.
3. **Choose who can see a page.** Use the list on its row: **Only you (operator)**, **Your customers** or **Everyone, signed in or not**. COB never answers a customer from a page that only you can see. The default for a new page is **Your customers**.
4. **Delete a page.** Choose **Delete** on its row and type the page name to confirm. COB stops answering from it.
5. **Read the built-in docs again.** On **Built-in docs** choose **Resync now**.

## Good to know

- An engine upgrade re-reads the built-in pages only. Pages you added are never changed or deleted.
- If your page and a built-in page disagree about how the engine works, COB follows the built-in page. Your pages are the source for your own business: support hours, prices, contacts.
- Your pages also appear in the Docs section under **From your platform**, to the people you allowed.
- Built-in pages cannot be edited or deleted; trying gives "That page comes with the engine and updates itself".

## Troubleshooting

- **COB does not use my new page yet**: ingestion takes a moment. Check again in a minute.
- **A customer cannot see a page**: check that it is set to **Your customers** or **Everyone**.
- **Resync does nothing**: a sync is already queued or running.

## Related

- [Engine info](/docs/screens/engine)
- [Documents](/docs/screens/documents)
