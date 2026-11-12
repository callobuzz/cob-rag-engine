---
audience: public
title: "The licence"
topic: plans
summary: "cob-rag-engine is open source under AGPL-3.0, with MIT for the SDKs and the widget scripts. What that lets you do, when you need a commercial licence, whether you can charge businesses, the Powered by line, and how to contribute."
starters:
  - "Which licence is cob-rag-engine under?"
  - "Do I need a commercial licence?"
  - "Can I charge businesses for chatbots built on it?"
  - "How do I hide the Powered by line?"
  - "How do I contribute?"
---
cob-rag-engine is open source. There is no activation step and no licence check. Every feature works for everyone. This page says what the licence means, in plain words.

## Which licence is this?

The engine and the dashboard are under the **GNU AGPL-3.0**. You can run them, read them, change them and share them.

The client libraries in `clients/` (JavaScript and Python) and the website widget scripts are under the **MIT** licence. You can put them in your own closed products.

## Do I need a commercial licence?

Most people do not. You can run the engine for yourself or for your customers under AGPL-3.0.

The AGPL-3.0 has one condition that matters on a server, section 13. If you change the engine and let other people use your changed version over a network, you must offer those people the source code of your version. If you do not change the engine, section 13 asks nothing of you.

A company that does not want this condition can ask Callobuzz for a commercial licence: https://callobuzz.com/contact.

## Can I charge businesses for chatbots built on it?

Yes. Charging businesses is allowed under AGPL-3.0 and under the commercial licence. Plans, wallets, top-ups and customer billing are in the product and work for everyone.

## What about the "Powered by" line?

The chat window shows "cob-rag-engine by Callobuzz" as plain text by default. The operator can hide it: open **Branding** and turn on **Hide "Powered by"**. It is a setting, not a licence feature.

## Does it send data to Callobuzz?

No. The product makes no call home. Your businesses, their visitors, questions and documents stay on your server. The only outside calls are the ones you set up yourself, such as your AI provider.

## Contributing

Every contribution is made under the contributor agreement in `CLA.md`. When you open a pull request, a bot asks you to sign once by writing a comment. `CONTRIBUTING.md` has the setup and the checks to run.
