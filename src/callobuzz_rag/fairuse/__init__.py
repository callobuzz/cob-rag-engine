"""Fair use (PIP3): per-business limits, the one decision for every answer, and the alerts.

`limits` holds the schemas and the effective values (plan, then the business's overrides),
`usage` reads how much is used, `decision` is the pure function that turns both into allow,
downgrade, pause or refuse, `gate` runs it at every answering route, `counters` and `alerts`
write what the decisions leave behind, and `rails` holds the engine-wide spend and crawl limits.
"""
