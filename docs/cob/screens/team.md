---
route: /team
audience: customer
title: "Team"
topic: keys
summary: "Invite teammates, change roles and remove people. Owners can do everything; members cannot manage keys or delete."
starters:
  - "How do I invite a teammate to my workspace?"
  - "What can a member not do?"
  - "Why can't I leave the workspace?"
  - "How long does an invitation last?"
---
Team lists everyone in your workspace who uses the dashboard. Each person logs in with their own e-mail and password. Members can see the list; only owners can invite, remove or change people.

## What you see

- **Members**: each person with their **Role** (**Owner** or **Member**) and when they were **Last active** ("Never logged in" until they accept). Your own row says "you".
- **Pending invitations**: the person's e-mail, the role they were invited as, who invited them and when the link expires. Each link works once and lasts 7 days.
- **What each role can do**, a short table. Both roles can use the playground, upload documents, crawl, and edit prompts and search settings. Only owners can create or delete knowledge bases, delete documents, manage API keys, widget keys and the model key, and invite, remove or change teammates. Members can view the plan and usage.
- A banner for members: "You're a Member. Only owners can invite, remove or change teammates, manage keys and delete things."

## Common tasks

1. **Invite someone.** **Invite teammate**, enter their e-mail (for example asha@shree-labs.in), choose **Member** (uses everything, no keys or deleting) or **Owner** (full control), **Send invitation**. They get an e-mail with a link; if they have no account they choose a name and password when they accept.
2. **Resend or revoke.** On a pending invitation choose **Resend** (a new link, 7 more days) or **Revoke** (the link stops working at once).
3. **Make someone an owner.** On their row choose **Make owner**. To go back, **Make member**.
4. **Remove a person.** **Remove** signs them out at once; documents they uploaded stay.
5. **Leave a workspace.** On your own row choose **Leave workspace**. You are signed out of it and must be invited again to return.

A workspace always keeps at least one owner. The only owner cannot leave or become a member until someone else is made an owner.

## Troubleshooting

- **`owner_required`**: only owners manage the team. Ask an owner.
- **`last_owner`**: make someone else an owner first.
- **`already_member`**: that person is already in the workspace.
- **`invitation_invalid`**: the link expired or was used. Resend it from here.
- **"Email isn't set up on this engine"** (`email_not_configured`): ask the platform operator to add them for you, or to set up e-mail.
- **`rate_limited`**: too many invitations too fast; wait the time shown.

## Related

- [Accept an invitation](/docs/screens/invite)
- [Profile & sessions](/docs/screens/profile)
- [Plan & billing](/docs/screens/plan)
