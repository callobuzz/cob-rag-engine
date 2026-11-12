# Support

cob-rag-engine is open source under AGPL-3.0. Community support runs through GitHub issues, best
effort. Commercial support is available by contract: https://callobuzz.com/contact.

## What is included

- Questions about how a feature works, answered in a GitHub issue when a maintainer has time.
- Bug reports: if the product does not do what the documentation says, open an issue.
- Updates: every release is free. `deploy/upgrade.sh` installs it.

## How to ask

Open an issue in the repository. Please include:

- the version (in `CHANGELOG.md` and in the dashboard);
- what you did, what happened and what you expected;
- the last lines of the log if the server is involved:
  `sudo docker compose --project-directory /opt/cob-rag/current logs --tail 100 app`
  (remove anything private first).

Security problems follow a separate process: see `SECURITY.md`. Please do not post them in a
public issue.

## Commercial support

A company that needs a response time, installation help, custom features or a commercial licence
instead of AGPL-3.0 can ask Callobuzz: https://callobuzz.com/contact. Commercial support is by
contract. This page makes no promise of a response time for community support.

## Where to read more

The online copy of the documentation is at https://callobuzz.github.io/cob-rag-engine/support.html.
