---
route: /kbs/$kbId
audience: customer
title: "Knowledge base settings"
topic: knowledge-bases
summary: "One knowledge base's overview, model, assistant, search quality, embedding model, keys and danger zone."
starters:
  - "Where do I change the model this knowledge base answers with?"
  - "How do I use my own provider key?"
  - "What is the difference between Read, Write and Full keys?"
  - "How do I publish changes to the assistant?"
---
Everything about one knowledge base lives here, in seven tabs. Members can read every tab and change prompts and search settings; only owners can change keys, delete, publish and restore versions. A tab with unsaved changes asks before you leave it.

## What you see

Along the top: the knowledge base name, **Open playground** and **Add content** buttons, and seven tabs: Overview, Model & keys, Assistant, Retrieval, Embedding, API keys and Danger zone. Each is described below.

## Overview tab

Documents and passages, questions in the last seven days compared with the week before, average answer confidence, last activity and creation date, and links to Documents, the Website widget and Agents (MCP).

## Model & keys tab

- **Answer model**: provider, model, a **Fast model** that rewrites search questions and sorts each message (use a small non-thinking model), and **Thinking** (Off is fastest and cheapest; turn it on for hard questions). **Temperature** and **Longest reply** are optional.
- **Your own model key (BYOK)**: paste your provider key to pay your provider directly for this knowledge base. It is stored encrypted and never shown again; you can **Replace** or **Clear** it.
- **When your plan does not include your own key** <!-- P5 plans -->: the key form is replaced by a note, "Using your own AI key is part of Pro, or available as an add-on", with a link to **Plan & billing**. A key saved earlier stays saved but is not used; answers then use your provider's AI and your balance, and you can still remove the key. An add-on or a plan that includes it switches the form back on.
- **Which key pays for an answer**: first match wins. A key sent with the request by an app, then this knowledge base's own key, then the platform's default key.
- **How the assistant speaks**: classic **Instructions**, **Rules** and **Reply language**. These apply only while the assistant (next tab) is switched off.
- **Model per language**: pick a different answer model for Hindi and Hinglish.

## Assistant tab

The assistant's identity, voice, languages, topics and limits, guidance rules, conversation starters, leads, talk to a person, expert instructions, versions, tests and a preview chat. Changes save to a **draft**; visitors see nothing until an owner presses **Publish**. **Discard draft** throws the changes away. **Versions** lists every published version; an owner can **Restore** one, which publishes it as a new version. You can switch the assistant off, which returns raw grounded answers for API use. **Guided setup** starts the wizard. Under **Talk to a person**, **Message when answers are paused** is what visitors read, next to your contact options, if the workspace's balance runs out; leave it as it is unless you want your own words.

## Retrieval tab

Pick **Economy**, **Standard** or **Premium**, then fine-tune under **Advanced**. Anything you change yourself wins over the preset. **Try in playground** opens the playground with your settings.

## Embedding tab

The model that turns text into searchable meaning. Changing it starts a migration: a new index is built beside the old one, checked, then switched, and answers keep working. The platform operator approves the migration on the Migrations screen.

## API keys tab

Named keys for apps and agents. Each has a **Permission**: **Read** (search and ask only), **Write** (also add and change content, no deleting) or **Full** (everything, including deleting and managing keys). You can **Edit**, **Rotate** or **Revoke** a key. The key text is shown once. Widget keys are listed below with a link to manage them.

## Danger zone tab

**Delete this knowledge base** removes its documents, search index, widget keys, usage history, conversations and memories for good. You must type the exact name to confirm.

## Common tasks

1. **Use your own provider key.** Model & keys, **Set a key**, paste it, **Save changes**. Answers for this knowledge base are billed to your provider.
2. **Change how the assistant sounds.** Assistant tab, open Voice, change tone or length, **Save draft**, check the Preview, then **Publish**.
3. **Make a key for one app.** API keys, **Create key**, name it "Agent - Claude Code", pick **Read**, copy the key when it appears.
4. **Rotate a leaked key.** API keys, **Rotate** on that row. The old key stops working at once; update every app that used it.
5. **Change the embedding model.** Embedding tab, choose the model, **Change model**. The migration waits for approval.

## Troubleshooting

- **`owner_required`**: the button is owner-only. Ask an owner.
- **`key_permission_denied`**: the key you are using has less permission than the action needs. Raise its permission on the API keys tab.
- **`assistant_draft_conflict`**: someone else saved the draft a moment ago. Load their version, then redo your change.
- **`assistant_rule_budget` or `assistant_prompt_budget`**: the guidance rules or instructions are too long. Shorten or turn some off.
- **`encryption_not_configured`**: leads and stored keys need an encryption key set by the platform operator.
- **`assistant_llm_unavailable`**: no model is set up. Add one on Model & keys.
- **`confirm_mismatch`**: the typed name must match the knowledge base name exactly.

## Tabs your plan does not include

Your provider decides, plan by plan, how much of the technical settings a business sees. If your plan hides them, **Model & keys**, **Retrieval**, **Embedding** and **API keys** are missing from the tabs, and opening one by its link says "Not included in your plan" and who to ask. The **Assistant** tab is always there.

## Related

- [Knowledge bases](/docs/screens/kbs)
- [Guided assistant setup](/docs/screens/kb-setup)
- [Website widget](/docs/screens/widget)
- [Agents (MCP)](/docs/screens/agents)
- [Migrations](/docs/screens/migrations)
