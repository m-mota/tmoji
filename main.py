import asyncio
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

DEFAULT_TIMEOUT = 10
DEFAULT_SHOW_EXPIRY = True
SETTINGS_DB = Path(__file__).with_name("bot_settings.sqlite3")
MAX_TIMEOUT = 2_147_483_647

load_dotenv()
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")

logging.basicConfig(level=logging.INFO)

with sqlite3.connect(SETTINGS_DB) as connection:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            timeout INTEGER NOT NULL,
            show_expiry INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    settings_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(guild_settings)")
    }
    if "show_expiry" not in settings_columns:
        connection.execute(
            "ALTER TABLE guild_settings ADD COLUMN show_expiry INTEGER NOT NULL DEFAULT 1"
        )
    if "emoji_name" in settings_columns:
        connection.execute(
            """
            CREATE TABLE guild_settings_new (
                guild_id INTEGER PRIMARY KEY,
                timeout INTEGER NOT NULL,
                show_expiry INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO guild_settings_new (guild_id, timeout, show_expiry)
            SELECT guild_id, timeout, show_expiry FROM guild_settings
            """
        )
        connection.execute("DROP TABLE guild_settings")
        connection.execute(
            "ALTER TABLE guild_settings_new RENAME TO guild_settings"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS active_emojis (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            slot INTEGER NOT NULL,
            emoji_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id),
            UNIQUE (guild_id, slot),
            UNIQUE (guild_id, emoji_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_roles (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, role_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS allowed_role_settings (
            guild_id INTEGER PRIMARY KEY
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO allowed_role_settings (guild_id)
        SELECT DISTINCT guild_id FROM allowed_roles
        """
    )


def get_guild_settings(guild_id: int) -> tuple[int, bool]:
    with sqlite3.connect(SETTINGS_DB) as connection:
        row = connection.execute(
            "SELECT timeout, show_expiry FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
    if row is None:
        return DEFAULT_TIMEOUT, DEFAULT_SHOW_EXPIRY
    return int(row[0]), bool(row[1])


def save_guild_settings(
    guild_id: int,
    timeout: int,
    show_expiry: bool,
) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        connection.execute(
            """
            INSERT INTO guild_settings (guild_id, timeout, show_expiry)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                timeout = excluded.timeout,
                show_expiry = excluded.show_expiry
            """,
            (guild_id, timeout, int(show_expiry)),
        )


def reset_guild_settings(guild_id: int) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        connection.execute(
            "DELETE FROM guild_settings WHERE guild_id = ?",
            (guild_id,),
        )


def get_allowed_role_ids(guild_id: int) -> set[int]:
    with sqlite3.connect(SETTINGS_DB) as connection:
        configured = connection.execute(
            "SELECT 1 FROM allowed_role_settings WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        if configured is None:
            return {guild_id}
        rows = connection.execute(
            "SELECT role_id FROM allowed_roles WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
    return {int(row[0]) for row in rows}


def configure_allowed_roles(connection: sqlite3.Connection, guild_id: int) -> None:
    cursor = connection.execute(
        "INSERT OR IGNORE INTO allowed_role_settings (guild_id) VALUES (?)",
        (guild_id,),
    )
    if cursor.rowcount:
        connection.execute(
            "INSERT OR IGNORE INTO allowed_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, guild_id),
        )


def add_allowed_role(guild_id: int, role_id: int) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        configure_allowed_roles(connection, guild_id)
        connection.execute(
            "INSERT OR IGNORE INTO allowed_roles (guild_id, role_id) VALUES (?, ?)",
            (guild_id, role_id),
        )


def remove_allowed_role(guild_id: int, role_id: int) -> bool:
    with sqlite3.connect(SETTINGS_DB) as connection:
        configure_allowed_roles(connection, guild_id)
        cursor = connection.execute(
            "DELETE FROM allowed_roles WHERE guild_id = ? AND role_id = ?",
            (guild_id, role_id),
        )
    return cursor.rowcount > 0


def clear_allowed_roles(guild_id: int) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO allowed_role_settings (guild_id) VALUES (?)",
            (guild_id,),
        )
        connection.execute(
            "DELETE FROM allowed_roles WHERE guild_id = ?",
            (guild_id,),
        )


def get_active_emoji_rows(
    guild_id: int | None = None,
) -> list[tuple[int, int, int, int]]:
    with sqlite3.connect(SETTINGS_DB) as connection:
        if guild_id is None:
            rows = connection.execute(
                "SELECT guild_id, user_id, slot, emoji_id FROM active_emojis"
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT guild_id, user_id, slot, emoji_id
                FROM active_emojis WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchall()
    return [(int(g), int(u), int(s), int(e)) for g, u, s, e in rows]


def save_active_emoji(
    guild_id: int,
    user_id: int,
    slot: int,
    emoji_id: int,
) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        connection.execute(
            """
            INSERT INTO active_emojis (guild_id, user_id, slot, emoji_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                slot = excluded.slot,
                emoji_id = excluded.emoji_id
            """,
            (guild_id, user_id, slot, emoji_id),
        )


def delete_active_emoji(guild_id: int, user_id: int, emoji_id: int) -> None:
    with sqlite3.connect(SETTINGS_DB) as connection:
        connection.execute(
            """
            DELETE FROM active_emojis
            WHERE guild_id = ? AND user_id = ? AND emoji_id = ?
            """,
            (guild_id, user_id, emoji_id),
        )


@dataclass(slots=True)
class ActiveEmoji:
    guild: discord.Guild
    interaction: discord.Interaction
    emoji: discord.Emoji
    slot: int
    user_id: int
    timeout: int
    show_expiry: bool
    expiry_task: asyncio.Task[None] | None = None


active_emojis: dict[tuple[int, int], ActiveEmoji] = {}
guild_locks: dict[int, asyncio.Lock] = {}
startup_recovery_complete = asyncio.Event()
recently_deleted_emoji_ids: set[int] = set()


def get_guild_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in guild_locks:
        guild_locks[guild_id] = asyncio.Lock()
    return guild_locks[guild_id]


def lowest_available_slot(used_slots: set[int]) -> int:
    slot = 1
    while slot in used_slots:
        slot += 1
    return slot


class EmojiBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.tree.sync()


intents = discord.Intents.default()
intents.guild_messages = True
intents.typing = True
proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
bot = EmojiBot(command_prefix="!", intents=intents, proxy=proxy)


@bot.event
async def on_ready() -> None:
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="temporary emojis :3",
        )
    )
    if not startup_recovery_complete.is_set():
        await clean_up_previous_sessions()
        startup_recovery_complete.set()
    logging.info("Logged in as %s (ID: %s)", bot.user, bot.user.id if bot.user else "unknown")


async def clean_up_previous_sessions() -> None:
    for guild_id, user_id, _slot, emoji_id in get_active_emoji_rows():
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        try:
            emoji = await guild.fetch_emoji(emoji_id)
            await emoji.delete(reason="Cleaning up temporary emoji after bot restart")
        except discord.NotFound:
            logging.info(
                "Temporary emoji %s was already missing during startup cleanup "
                "(guild_id=%s, user_id=%s)",
                emoji_id,
                guild_id,
                user_id,
            )
        except discord.HTTPException:
            logging.exception(
                "Failed to clean up old temporary emoji %s "
                "(guild_id=%s, user_id=%s)",
                emoji_id,
                guild_id,
                user_id,
            )
            continue
        recently_deleted_emoji_ids.add(emoji_id)
        delete_active_emoji(guild_id, user_id, emoji_id)
        logging.info(
            "Cleaned up temporary emoji %s after restart "
            "(guild_id=%s, user_id=%s)",
            emoji_id,
            guild_id,
            user_id,
        )


def is_administrator(interaction: discord.Interaction) -> bool:
    return interaction.permissions.administrator


def role_mention(role: discord.Role) -> str:
    if role.is_default():
        return "@everyone"
    return role.mention


@bot.tree.command(name="settings", description="view this server's settings")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def settings(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return

    timeout, show_expiry = get_guild_settings(guild.id)
    allowed_role_ids = get_allowed_role_ids(guild.id)
    logging.info(
        "Settings viewed (guild_id=%s, user_id=%s)",
        guild.id,
        interaction.user.id,
    )
    role_names = []
    for role_id in sorted(allowed_role_ids):
        role = guild.get_role(role_id)
        role_names.append(
            role_mention(role) if role is not None else f"deleted role {role_id}"
        )
    roles_summary = "\n- ".join(role_names) if role_names else "none configured"
    await interaction.response.send_message(
        f"**timeout:** {timeout} seconds\n"
        f"**show expiry:** {show_expiry}\n"
        f"**allowed roles:**\n- {roles_summary}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="set", description="change this server's timeout and expiry notice")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    timeout="seconds without typing before deleting the emoji",
    show_expiry="whether to send you an expiry message",
)
async def set_settings(
    interaction: discord.Interaction,
    timeout: int | None = None,
    show_expiry: bool | None = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return
    if timeout is None and show_expiry is None:
        await interaction.response.send_message(
            "choose a timeout or show expiry option to update!",
            ephemeral=True,
        )
        return
    if timeout is not None and not 1 <= timeout <= MAX_TIMEOUT:
        await interaction.response.send_message(
            f"timeout must be between 1 and {MAX_TIMEOUT} seconds!",
            ephemeral=True,
        )
        return
    current_timeout, current_show_expiry = get_guild_settings(guild.id)
    updated_timeout = timeout if timeout is not None else current_timeout
    updated_show_expiry = (
        show_expiry if show_expiry is not None else current_show_expiry
    )
    save_guild_settings(
        guild.id,
        updated_timeout,
        updated_show_expiry,
    )
    logging.info(
        "Settings updated (guild_id=%s, user_id=%s, timeout=%s, show_expiry=%s)",
        guild.id,
        interaction.user.id,
        updated_timeout,
        updated_show_expiry,
    )
    await interaction.response.send_message(
        f"settings saved!\n"
        f"**timeout:** {updated_timeout} seconds\n"
        f"**show expiry:** {updated_show_expiry}",
        ephemeral=True,
    )


@bot.tree.command(name="reset", description="restore this server's default settings")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def reset_settings(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return

    reset_guild_settings(guild.id)
    logging.info(
        "Settings reset to defaults (guild_id=%s, user_id=%s)",
        guild.id,
        interaction.user.id,
    )
    await interaction.response.send_message(
        f"settings reset to their defaults!\n"
        f"**timeout:** {DEFAULT_TIMEOUT} seconds\n"
        f"**show expiry:** {DEFAULT_SHOW_EXPIRY}",
        ephemeral=True,
    )


@bot.tree.command(name="add", description="allow a role to use the emoji command")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(role="the role to allow")
async def add_allowed_role_command(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return

    add_allowed_role(guild.id, role.id)
    logging.info(
        "Allowed role added (guild_id=%s, role_id=%s, user_id=%s)",
        guild.id,
        role.id,
        interaction.user.id,
    )
    await interaction.response.send_message(
        f"{role_mention(role)} can now use the emoji command!",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="remove", description="remove a role from emoji access")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(role="the role to remove")
async def remove_allowed_role_command(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return

    removed = remove_allowed_role(guild.id, role.id)
    logging.info(
        "Allowed role removal requested (guild_id=%s, role_id=%s, "
        "user_id=%s, removed=%s)",
        guild.id,
        role.id,
        interaction.user.id,
        removed,
    )
    if removed:
        response = f"{role_mention(role)} can no longer use the emoji command!"
    else:
        response = f"{role_mention(role)} wasn't on the allowlist!"
    await interaction.response.send_message(
        response,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )

@bot.tree.command(name="clear", description="remove all roles allowed to use the emoji command")
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def clear_allowed_roles_command(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return
    if not is_administrator(interaction):
        await interaction.response.send_message(
            "you need the **administrator** permission to manage these settings!",
            ephemeral=True,
        )
        return

    clear_allowed_roles(guild.id)
    logging.info(
        "Allowed roles cleared (guild_id=%s, user_id=%s)",
        guild.id,
        interaction.user.id,
    )
    await interaction.response.send_message(
        "all allowed roles were removed, so only administrators can use the emoji command now!",
        ephemeral=True,
    )


async def delete_temporary_emoji(
    session: ActiveEmoji,
) -> None:
    guild = session.guild
    user_id = session.user_id

    def is_user_typing(channel, user, _when) -> bool:
        channel_guild = getattr(channel, "guild", None)
        return (
            user.id == user_id
            and channel_guild is not None
            and channel_guild.id == guild.id
        )

    def is_user_message(message: discord.Message) -> bool:
        return (
            message.author.id == user_id
            and message.guild is not None
            and message.guild.id == guild.id
        )

    while True:
        typing_task = asyncio.create_task(bot.wait_for("typing", check=is_user_typing))
        message_task = asyncio.create_task(bot.wait_for("message", check=is_user_message))
        done, pending = await asyncio.wait(
            (typing_task, message_task),
            timeout=session.timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        if not done or message_task in done:
            expiry_reason = "inactivity timeout" if not done else "user message"
            break

    key = (guild.id, user_id)
    async with get_guild_lock(guild.id):
        if active_emojis.get(key) is not session:
            return
        try:
            await session.emoji.delete(
                reason=(
                    "Temporary emoji expired after a message or "
                    f"{session.timeout} seconds without typing"
                )
            )
        except discord.NotFound:
            logging.info(
                "Temporary emoji %s was already missing at expiry "
                "(guild_id=%s, user_id=%s)",
                session.emoji.id,
                guild.id,
                user_id,
            )
        except discord.HTTPException:
            logging.exception(
                "Failed to delete temporary emoji `%s` "
                "(guild_id=%s, user_id=%s)",
                session.emoji,
                guild.id,
                user_id,
            )
            return

        recently_deleted_emoji_ids.add(session.emoji.id)
        active_emojis.pop(key, None)
        delete_active_emoji(guild.id, user_id, session.emoji.id)
        logging.info(
            "Temporary emoji expired (guild_id=%s, user_id=%s, emoji_id=%s, "
            "reason=%s)",
            guild.id,
            user_id,
            session.emoji.id,
            expiry_reason,
        )
        if session.show_expiry:
            try:
                await session.interaction.followup.send(
                    f"your temporary emoji `{session.emoji}` expired!",
                    ephemeral=True,
                )
            except discord.HTTPException:
                logging.exception(
                    "Failed to send expiry notice for temporary emoji `%s` "
                    "(guild_id=%s, user_id=%s)",
                    session.emoji,
                    guild.id,
                    user_id,
                )


@bot.tree.command(name="e", description="get a temporary emoji from an image")
@app_commands.guild_only()
@app_commands.describe(image="the image to use as an emoji")
async def set_emoji(
    interaction: discord.Interaction,
    image: discord.Attachment,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "this command only works in a server!",
            ephemeral=True,
        )
        return

    bot_member = guild.me
    if bot_member is None:
        await interaction.response.send_message(
            "i couldn't verify the bot's permissions in this server!",
            ephemeral=True,
        )
        return

    if not bot_member.guild_permissions.manage_emojis_and_stickers:
        await interaction.response.send_message(
            "the bot needs **manage emojis and stickers** permission in this server!",
            ephemeral=True,
        )
        return

    if not interaction.permissions.administrator:
        allowed_role_ids = get_allowed_role_ids(guild.id)
        member = interaction.user
        if not allowed_role_ids or not isinstance(member, discord.Member) or not any(
            role.id in allowed_role_ids for role in member.roles
        ):
            await interaction.response.send_message(
                "you need an allowed role to use this command, ask an administrator to configure `/add`!",
                ephemeral=True,
            )
            return

    if image.content_type is None or not image.content_type.lower().startswith("image/"):
        await interaction.response.send_message(
            "please attach an image!",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    try:
        image_data = await image.read()
    except discord.HTTPException:
        logging.exception(
            "Failed to read image attachment (guild_id=%s, user_id=%s)",
            guild.id,
            interaction.user.id,
        )
        await interaction.followup.send(
            "i couldn't read the attached image, please try again!",
            ephemeral=True,
        )
        return

    await startup_recovery_complete.wait()
    timeout, show_expiry = get_guild_settings(guild.id)
    user_id = interaction.user.id
    key = (guild.id, user_id)

    async with get_guild_lock(guild.id):
        previous = active_emojis.get(key)
        if previous is not None and previous.expiry_task is not None:
            previous.expiry_task.cancel()
            await asyncio.gather(previous.expiry_task, return_exceptions=True)

        if previous is not None:
            slot = previous.slot
            try:
                await previous.emoji.delete(
                    reason=f"updated by {interaction.user} with /e",
                )
            except discord.NotFound:
                logging.info(
                    "Previous temporary emoji %s was already missing during "
                    "replacement (guild_id=%s, user_id=%s)",
                    previous.emoji.id,
                    guild.id,
                    user_id,
                )
            except discord.HTTPException:
                logging.exception(
                    "Failed to replace temporary emoji `%s` "
                    "(guild_id=%s, user_id=%s)",
                    previous.emoji,
                    guild.id,
                    user_id,
                )
                previous.interaction = interaction
                previous.timeout = timeout
                previous.show_expiry = show_expiry
                previous.expiry_task = asyncio.create_task(
                    delete_temporary_emoji(previous),
                    name=f"expire-temp-emoji-{previous.emoji.id}",
                )
                await interaction.followup.send(
                    "i couldn't update your current emoji, check the bot's permissions!",
                    ephemeral=True,
                )
                return
            recently_deleted_emoji_ids.add(previous.emoji.id)
            logging.info(
                "Temporary emoji replaced (guild_id=%s, user_id=%s, "
                "previous_emoji_id=%s)",
                guild.id,
                user_id,
                previous.emoji.id,
            )
        else:
            active_rows = get_active_emoji_rows(guild.id)
            used_slots = {row[2] for row in active_rows}
            for existing_emoji in guild.emojis:
                match = re.fullmatch(r"e([1-9][0-9]*)", existing_emoji.name)
                if (
                    match is not None
                    and existing_emoji.id not in recently_deleted_emoji_ids
                ):
                    used_slots.add(int(match.group(1)))
            slot = lowest_available_slot(used_slots)

        emoji_name = f"e{slot}"
        try:
            emoji = await guild.create_custom_emoji(
                name=emoji_name,
                image=image_data,
                reason=f"added by {interaction.user} with {bot.user} via /e",
            )
        except discord.HTTPException:
            logging.exception(
                "Discord rejected the request to create an emoji "
                "(guild_id=%s, user_id=%s, slot=%s)",
                guild.id,
                user_id,
                slot,
            )
            if previous is not None:
                active_emojis.pop(key, None)
                delete_active_emoji(guild.id, user_id, previous.emoji.id)
            await interaction.followup.send(
                "i couldn't add the emoji, check the image, emoji capacity, and bot permissions!",
                ephemeral=True,
            )
            return

        save_active_emoji(guild.id, user_id, slot, emoji.id)
        logging.info(
            "Temporary emoji created (guild_id=%s, user_id=%s, emoji_id=%s, "
            "slot=%s)",
            guild.id,
            user_id,
            emoji.id,
            slot,
        )
        session = ActiveEmoji(
            guild=guild,
            interaction=interaction,
            emoji=emoji,
            slot=slot,
            user_id=user_id,
            timeout=timeout,
            show_expiry=show_expiry,
        )
        active_emojis[key] = session
        session.expiry_task = asyncio.create_task(
            delete_temporary_emoji(session),
            name=f"expire-temp-emoji-{emoji.id}",
        )

    await interaction.followup.send(
        f"your temporary emoji is `:{emoji.name}:`!",
        ephemeral=True,
    )


bot.run(token)