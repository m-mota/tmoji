# <ins>T</ins>emporary e<ins>moji</ins>s

A Discord bot that turns an image attachment into a temporary custom emoji in
the current server.

## Requirements

- Python 3.10 or newer
- A Discord application/bot with the `applications.commands` scope
- The bot needs the **Manage Emojis and Stickers** permission in each server
- The bot must be able to receive guild message and typing events

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Fill the `.env` file in the project folder with your bot token:

```text
DISCORD_TOKEN=your_bot_token
```

Run the bot:

```powershell
python main.py
```

If the host requires an HTTPS proxy to reach Discord, set `HTTPS_PROXY` (or
`https_proxy`) to the proxy URL in the environment used to launch the bot. For
systemd services or containers, configure it in the service or container
environment; an interactive shell's environment may not be inherited.

Slash commands are synced when the bot starts. Global command updates may take
some time to appear in Discord.

## How it works

The user can request for a custom emoji with `/e image:<attachment>`. If there are enough server emoji slots available and permissions are met, an emoji named `:eN:` is created, where `N` is the position of the emoji in the request queue. Afterwards, the user is able to use the emoji in reactions or messages up until a set time of typing inactivity has elapsed or until the user sends any message.

Admins can use these commands to manage the bot:
- `/settings` shows the current server settings, including allowed roles.
- `/set timeout:<seconds> show_expiry:<bool>` updates settings.
- `/reset` restores the default settings.
- `/add role:<role>` allows a role to use `/e`.
- `/remove role:<role>` removes a role from the allowlist.
- `/clear` removes every role from the allowlist.

These admin commands require the **Administrator** permission. Settings are stored separately per server in `bot_settings.sqlite3`. If no settings have been saved for a server, the defaults are a 10-second timeout and expiry notices enabled. Non-admin users can use `/e` only if they are in the allowlist. With no roles configured, only administrators can use `/e`. The bot still requires the **Manage Emojis and Stickers** permission to create emojis. The database also tracks allowed roles and active emoji slots so the bot can clean up its temporary emojis when it restarts. The database is created automatically and is ignored by Git.
