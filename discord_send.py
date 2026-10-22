#!/usr/bin/env python3
import sys
import os
import json

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CONFIG_DIR = os.environ.get("GEMINI_CONFIG_DIR") or os.path.expanduser("~/.gemini/config")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

import discord_hook

CONFIG_FILE = os.path.join(CONFIG_DIR, "discord_config.json")
THREADS_FILE = os.path.join(CONFIG_DIR, "discord_threads.json")


def send_message(text, conv_id=None):
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    token = config["bot_token"]
    channel_id = config["channel_id"]

    if conv_id and os.path.exists(THREADS_FILE):
        try:
            with open(THREADS_FILE, "r", encoding="utf-8") as tf:
                threads = json.load(tf)
            if conv_id in threads and threads[conv_id].get("thread_id"):
                channel_id = threads[conv_id]["thread_id"]
        except Exception:
            pass

    discord_hook.send_discord_rich(token, channel_id, text, is_user_prompt=False)


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read()
    if msg.strip():
        send_message(msg)
        print("Sent message to Discord.")
