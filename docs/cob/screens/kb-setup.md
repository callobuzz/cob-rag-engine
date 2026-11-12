---
route: /kbs/$kbId/setup
audience: customer
title: "Guided assistant setup"
topic: assistant
summary: "A six-step wizard that reads your website or asks a few questions, writes your assistant, tests it and publishes it."
starters:
  - "How long does the guided setup take?"
  - "Does the guided setup use up my monthly questions?"
  - "Can I change things the wizard set up?"
  - "What happens to my existing assistant if I run setup again?"
---
Guided setup builds an assistant for a knowledge base in about 15 minutes. You give it your website address or pick a business type, confirm a few facts, answer some questions, and it writes the assistant's settings, tests it and publishes it. Everything it creates stays editable afterwards on the knowledge base's Assistant tab. Only owners can run it.

## What you see

A progress bar with six steps:

1. **Website**: choose **I have a website** (we read it and fill in what we find) or **Choose a business type** (clinic or lab, shop, coaching institute, software, real estate, government service, restaurant, general support). Starting again replaces an unfinished setup.
2. **Your business**: "Here's what we found". Check each value: business name, phone, WhatsApp number, e-mail, address, website and opening hours. Nothing is saved to your assistant until the end. If the website could not be read, the fields are blank for you to fill in.
3. **Questions**: a short interview, one question at a time ("Question 3 of 9"), such as who asks, which language they use and what to do when the assistant cannot answer. Questions the template already settles are skipped.
4. **Review**: a table of everything that was set up, where each item came from (your website, the template, your answer) and a link to edit it in the Assistant tab. Warnings tell you when something will not work yet, for example a contact form while lead capture is off.
5. **Checks**: the engine asks your assistant 15 to 25 test questions (some in Hinglish, some off topic, some sensitive) and compares what it would do with what it should do. A gap means your documents do not cover a question yet; you can add content or an exact answer from there.
6. **Publish**: makes this the assistant visitors talk to.

While the website is read, a counter shows "Reading your website... 4 of 12 pages". Tick **Also add these pages to the knowledge base** if you want the pages kept as documents; otherwise they are read for setup and thrown away.

## Common tasks

1. **Start the wizard.** Open the knowledge base, Assistant tab, **Guided setup**; or choose **Set up the assistant now** after creating a knowledge base.
2. **Set up from a website.** Enter `https://shreelabs.in`, **Continue**, wait for the pages to be read, then correct the details it found.
3. **Set up without a website.** Choose a business type and answer the questions. The wording then comes from the template.
4. **Fix a gap.** On Checks, a gap row offers **Add content** or **Add an exact answer**. After adding, **Run again**.
5. **Go live.** On Publish press **Publish**. Then try it in the playground or add it to your website.

Cost: the wizard never uses your monthly question allowance, because it does not answer visitors. Reading the website and writing the assistant use this knowledge base's AI model; the checks only test how questions are understood, so they are almost free.

## Troubleshooting

- **"No AI model is set up for this knowledge base, so we can't read your website automatically"**: add a model on the Model & keys tab, or choose a business type instead.
- **"We couldn't read any pages from that address"**: check the address starts with https:// and is public, or pick a business type.
- **"The website is taking too long"**: choose a business type, or try again later.
- **"This setup was replaced"** (`setup_session_not_found`): a newer setup was started for this knowledge base. Start again.
- **`setup_step_invalid`**: the step is not ready. Finish the current step first.
- **Your search model is English-only** while your visitors write Hindi or Hinglish: switch to a multilingual embedding model on the Embedding tab.
- **Leads are switched off** warning: the engine has no encryption key. Ask the platform operator to set one.

## Related

- [Knowledge base settings](/docs/screens/kb-detail)
- [Add content](/docs/screens/add)
- [Playground](/docs/screens/playground)
- [Website widget](/docs/screens/widget)
