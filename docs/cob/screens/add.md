---
route: /add
audience: customer
title: "Add content"
topic: knowledge-bases
summary: "Upload files, import records, and watch every upload and import as it is read, split and indexed."
starters:
  - "Which file types can I upload and how big can they be?"
  - "How do I import an FAQ or a price list from a spreadsheet?"
  - "Why did my upload fail?"
  - "What happens if I upload the same file twice?"
---
Add content puts new knowledge into the selected knowledge base. It has three tabs: **Upload files**, **Import records** and **Jobs**. Everyone in the workspace can add content; the document limit of your plan applies.

## What you see

The screen shows the selected knowledge base and three tabs, each described below. A banner tells you how many more documents your plan has room for.

## Upload files tab

Drop files on the drop area or choose **Choose files**. The screen tells you what this engine accepts and the largest size for one file, for example "Up to 50 MB each". Typical types are PDF, Word, Excel, PowerPoint, text, Markdown and HTML; audio only works when the platform operator has switched transcription on. Each file shows its own progress through the stages **Uploading**, **Reading**, **Splitting**, **Enriching**, **Indexing**, **Saving** and **Done**.

Under **Options** for a file: **How to add it** (automatic duplicate check, always as a new document, replace an existing document, or add as a new version of one), optional **Metadata** as a JSON object of text values such as `{"category": "prices"}`, and **Skip the duplicate check**.

## Import records tab

Records are text entries that already exist as data: FAQs, a price list, help articles. Paste JSON (an array of objects) or choose a `.json` or `.csv` file, then:

1. **Source**: read the records.
2. **Fields**: choose which column is the **ID** (unique; importing the same id again updates that record), which is the **Text** the assistant reads, and optionally **Title**, **URL** and metadata columns.
3. **Preview and import**: the first five records as they will be sent, the number of batches, and any rows with problems (missing id, empty text, too long). Rows with problems are left out. Choose **Import**.

Each batch reports "3 new, 1 updated, 12 unchanged".

## Jobs tab

Every upload and import with its **Status**, **Mode**, **Documents** progress, **Created** and **Finished** times. Select **Details** for the job id, errors and similar documents found. A failed upload has a **Retry** button (in the row and in Details): it runs the stored file again as a new job, with the same tags and review setting as the upload, so the failed one stays in the list. Once a retry is running or finished, the old row no longer offers Retry. A records import has no file, so it has no Retry. You can also ask COB to "retry the failed upload".

## Common tasks

1. **Upload a price list.** Upload files, drop `prices.pdf`, wait for **Done**, then open it from Documents.
2. **Import FAQs from a sheet.** Import records, choose the CSV, map `id`, `question` as Title and `answer` as Text, Import.
3. **Replace an old file.** Upload the new file, open Options, choose **Replace an existing document** and pick the old one.
4. **Check a failed upload.** Jobs tab, open Details and read the error. Fix the file and choose **Retry**, or upload it again.

## Troubleshooting

- **"This engine doesn't accept .xyz files"** or **"over the limit"**: convert the file or split it. The limits are shown on the drop area.
- **"Audio needs transcription, which is off on this engine"**: ask the platform operator to switch it on.
- **Failed while Reading**: the file may be password protected or damaged. Remove the password and retry.
- **`document_limit_reached`**: your plan allows N documents ("12 used; this adds 3"). Delete old documents or ask for a bigger plan.
- **"Already received"**: the engine already has this exact upload and shows its job instead of repeating it.
- **"Upload storage unavailable, try again"**: the engine's file storage did not answer; wait a minute.
- **Retry says "The stored file is gone"** (`job_not_retryable`): the file was removed from storage, so upload it again. The same code answers a job that did not fail, a records import, or an upload whose retry is already running (`already_retried`).
- **Needs review**: the file looks like an existing document. Decide on the Documents screen.

## Related

- [Documents](/docs/screens/documents)
- [Crawler](/docs/screens/crawler)
- [Content Portal](/docs/screens/portal)
- [Plan & billing](/docs/screens/plan)
