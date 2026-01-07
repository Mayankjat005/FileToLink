# Thunder/__main__.py

import asyncio
import glob
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# SAFE uvloop setup (Python 3.12 → enabled, Python 3.14 → disabled)
# ─────────────────────────────────────────────────────────────
try:
    from uvloop import install
    install()
    print("✓ uvloop enabled")
except Exception as e:
    print("⚠ uvloop disabled:", e)

from aiohttp import web
from pyrogram import idle
from pyrogram.errors import FloodWait, MessageNotModified

from Thunder import __version__
from Thunder.bot import StreamBot
from Thunder.bot.clients import cleanup_clients, initialize_clients
from Thunder.server import web_server
from Thunder.utils.commands import set_commands
from Thunder.utils.database import db
from Thunder.utils.keepalive import ping_server
from Thunder.utils.logger import logger
from Thunder.utils.messages import MSG_ADMIN_RESTART_DONE
from Thunder.utils.rate_limiter import rate_limiter, request_executor
from Thunder.utils.tokens import cleanup_expired_tokens
from Thunder.vars import Var


PLUGIN_PATH = "Thunder/bot/plugins/*.py"
VERSION = __version__


def print_banner():
    banner = f"""
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║   ████████╗██╗  ██╗██╗   ██╗███╗   ██╗██████╗ ███████╗██████╗     ║
║   ╚══██╔══╝██║  ██║██║   ██║████╗  ██║██╔══██╗██╔════╝██╔══██╗    ║
║      ██║   ███████║██║   ██║██╔██╗ ██║██║  ██║█████╗  ██████╔╝    ║
║      ██║   ██╔══██║██║   ██║██║╚██╗██║██║  ██║██╔══╝  ██╔══██╗    ║
║      ██║   ██║  ██║╚██████╔╝██║ ╚████║██████╔╝███████╗██║  ██║    ║
║      ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═════╝ ╚══════╝╚═╝  ╚═╝    ║
║                                                                   ║
║                  File Streaming Bot v{VERSION}                    ║
╚═══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


async def import_plugins():
    print("╠════════════════════ IMPORTING PLUGINS ════════════════════╣")
    plugins = glob.glob(PLUGIN_PATH)
    if not plugins:
        print("   ▶ No plugins found to import!")
        return 0

    success_count = 0
    failed_plugins = []

    for file_path in plugins:
        try:
            plugin_path = Path(file_path)
            plugin_name = plugin_path.stem
            import_path = f"Thunder.bot.plugins.{plugin_name}"

            spec = importlib.util.spec_from_file_location(import_path, plugin_path)
            if spec is None or spec.loader is None:
                logger.error(f"Invalid plugin specification for {plugin_name}")
                failed_plugins.append(plugin_name)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[import_path] = module
            spec.loader.exec_module(module)
            success_count += 1

        except Exception as e:
            logger.error(f"   ✖ Failed to import plugin {plugin_name}: {e}")
            failed_plugins.append(plugin_name)

    print(f"   ▶ Total: {len(plugins)} | Success: {success_count} | Failed: {len(failed_plugins)}")
    if failed_plugins:
        print(f"   ▶ Failed plugins: {', '.join(failed_plugins)}")

    return success_count


async def start_services():
    start_time = datetime.now()
    print_banner()
    print("╔════════════════ INITIALIZING BOT SERVICES ════════════════╗")

    # ── Telegram Bot ─────────────────────────────────────────
    try:
        try:
            await StreamBot.start()
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await StreamBot.start()

        try:
            bot_info = await StreamBot.get_me()
        except FloodWait as e:
            await asyncio.sleep(e.value)
            bot_info = await StreamBot.get_me()

        StreamBot.username = bot_info.username
        print(f"   ✓ Bot initialized successfully as @{StreamBot.username}")

        await set_commands()

        restart_message_data = await db.get_restart_message()
        if restart_message_data:
            try:
                await StreamBot.edit_message_text(
                    chat_id=restart_message_data["chat_id"],
                    message_id=restart_message_data["message_id"],
                    text=MSG_ADMIN_RESTART_DONE,
                )
                await db.delete_restart_message(restart_message_data["message_id"])
            except MessageNotModified:
                pass
            except FloodWait as e:
                await asyncio.sleep(e.value)

    except Exception as e:
        logger.error(f"Bot initialization failed: {e}", exc_info=True)
        return

    # ── Clients ─────────────────────────────────────────────
    await initialize_clients()
    await import_plugins()

    # ── Background tasks ────────────────────────────────────
    request_executor_task = asyncio.create_task(request_executor())
    keepalive_task = asyncio.create_task(ping_server())
    token_cleanup_task = asyncio.create_task(schedule_token_cleanup())

    # ── Web Server ──────────────────────────────────────────
    app_runner = web.AppRunner(await web_server())
    await app_runner.setup()
    site = web.TCPSite(app_runner, Var.BIND_ADDRESS, Var.PORT)
    await site.start()

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"   ✓ Bot running on {Var.BIND_ADDRESS}:{Var.PORT}")
    print(f"   ✓ Startup time: {elapsed:.2f}s")

    try:
        await idle()
    finally:
        for task in (request_executor_task, keepalive_task, token_cleanup_task):
            task.cancel()

        await cleanup_clients()
        await rate_limiter.shutdown()
        await app_runner.cleanup()


async def schedule_token_cleanup():
    while True:
        try:
            await asyncio.sleep(3 * 3600)
            await cleanup_expired_tokens()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Token cleanup error: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────
# MODERN PYTHON ENTRYPOINT (Python 3.11+ SAFE)
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        asyncio.run(start_services())
    except KeyboardInterrupt:
        print("Bot stopped by user (CTRL+C)")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
