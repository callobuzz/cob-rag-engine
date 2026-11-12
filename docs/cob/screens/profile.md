---
route: /profile
audience: customer
title: "Profile & sessions"
topic: keys
summary: "Your name, your password and the devices you are signed in on, with sign-out for each."
starters:
  - "How do I change my password?"
  - "How do I sign out of all my other devices?"
  - "What are the password rules?"
  - "Why does this page say I'm signed in with an API key?"
---
Profile & sessions is about you, not the workspace. It is the same for owners and members. Open it from the account menu.

## What you see

- **Profile**: your **Name** (editable, up to 255 characters), your **Email** (read only; it is your sign-in) and a tag showing the e-mail is verified.
- **Change password**: your current password, a new password with a strength meter, and the requirements: 10 to 128 characters, not your e-mail name and not a very common password. Changing it signs you out on every other device.
- **Where you're signed in**: a list of sessions with the device, the address it came from, when it was last used and a **Current** tag on this one. Sessions last 30 days unless you sign out. **Sign out** ends one; **Sign out everywhere else** ends all but this one. **Log out** ends this one.
- **If you signed in with an API key**: the screen says so and explains that keys have no profile, password or sessions. It tells you whether it is the engine's admin key or a workspace key, and suggests logging in with your e-mail to change a name or password.

## Common tasks

1. **Change your name.** Edit Name, **Save**. It updates everywhere your team sees it.
2. **Change your password.** Enter the current one and a new one, **Change password**. Other sessions are signed out.
3. **Lock out a lost phone.** Find its session, **Sign out**, then change your password.
4. **Leave a shared computer.** **Log out** on that session.

## Troubleshooting

- **"That isn't your current password"**: check Caps Lock and try again.
- **`weak_password`**: the message lists why (too short, too long, too common, contains your e-mail name). Choose another.
- **`session_required`**: "This needs an email sign-in; you're using an API key." Log in with your e-mail.
- **"Don't recognise one?"**: sign that session out and change your password.
- **"Couldn't load your sessions"**: reload.

## Related

- [Sign in](/docs/screens/login)
- [Team](/docs/screens/team)
- [Settings](/docs/screens/settings)
