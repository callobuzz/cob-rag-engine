"""Website learning: the engine reads a business's own site (BIZ1).

Only sites the owner listed as allowed sites of a widget key are read, politely (robots.txt,
`noindex`, `nofollow`, a delay between requests) and never anything private (`netguard`). Pages
go through the normal records pipeline, so they behave like any other document.
"""
