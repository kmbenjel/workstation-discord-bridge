#!/usr/bin/env python3
import sys
import os
import re
import time
import json
import uuid
import urllib.request
import urllib.error
import fcntl

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CONFIG_DIR = os.environ.get("GEMINI_CONFIG_DIR") or os.path.expanduser("~/.gemini/config")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

import discord_hook
import discord_status
import discord_manager

CONFIG_FILE = os.path.join(CONFIG_DIR, "discord_config.json")
THREADS_FILE = os.path.join(CONFIG_DIR, "discord_threads.json")
LISTENER_STATE_FILE = os.path.join(CONFIG_DIR, "discord_listener_state.json")
LOG_FILE = os.path.join(CONFIG_DIR, "discord_listener.log")
BRAIN_DIR = os.environ.get("ANTIGRAVITY_BRAIN_DIR") or os.path.expanduser("~/.gemini/antigravity/brain")


def log_message(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def download_attachment(att_url, filename="attachment.dat"):
    ext = os.path.splitext(filename)[1].lower()
    is_audio = ext in (".ogg", ".mp3", ".m4a", ".wav", ".aac", ".flac")
    sub_dir = "discord_audio" if is_audio else "discord_attachments"
    scratch_dir = os.path.join("/home/k/.gemini/antigravity/scratch", sub_dir)
    os.makedirs(scratch_dir, exist_ok=True)
    ts = int(time.time())
    safe_name = os.path.basename(filename) or ("voice.ogg" if is_audio else "file.dat")
    local_name = f"{ts}_{safe_name}"
    local_path = os.path.join(scratch_dir, local_name)
    try:
        req = urllib.request.Request(att_url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        with urllib.request.urlopen(req, timeout=30) as resp, open(local_path, "wb") as f:
            f.write(resp.read())
        return local_path, is_audio
    except Exception as e:
        log_message(f"Failed to download attachment {filename}: {e}")
        return None, is_audio


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def api_request(token, method, path, data=None):
    url = f"https://discord.com/api/v10{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBot (https://antigravity.google, 2.0)",
    }
    body = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        if raw:
            return json.loads(raw.decode("utf-8"))
        return None


def get_current_conv_id():
    env_cid = os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
    if env_cid:
        return env_cid
    try:
        if os.path.exists(BRAIN_DIR):
            dirs = [
                d for d in os.listdir(BRAIN_DIR)
                if os.path.isdir(os.path.join(BRAIN_DIR, d)) and len(d) > 20
            ]
            if dirs:
                latest = max(dirs, key=lambda d: os.path.getmtime(os.path.join(BRAIN_DIR, d)))
                return latest
    except Exception:
        pass
    return None


def create_thread_for_general_message(token, general_channel_id, msg, author_id, prompt_text, has_attachments=False):
    """Creates a dedicated Discord thread off a message posted in #general, matching the prompt's language."""
    new_conv_id = uuid.uuid4().hex[:12]

    clean_prompt = prompt_text.strip()
    if clean_prompt.startswith("!new") or clean_prompt.startswith("/new"):
        clean_prompt = clean_prompt[4:].strip()

    is_ar = discord_hook.is_arabic_text(clean_prompt)
    if not clean_prompt:
        if is_ar:
            clean_prompt = "تسجيل صوتي أو مرفق" if has_attachments else "محادثة جديدة"
        else:
            clean_prompt = "Voice or File Attachment" if has_attachments else "New Session"

    short_title = clean_prompt[:60].replace("\n", " ").strip()
    thread_name = f"💬 {short_title}"
    msg_id = msg.get("id")

    thread_id = None
    starter_id = msg_id

    if msg_id:
        try:
            thread_obj = api_request(
                token,
                "POST",
                f"/channels/{general_channel_id}/messages/{msg_id}/threads",
                {"name": thread_name[:100], "auto_archive_duration": 10080}
            )
            if thread_obj and thread_obj.get("id"):
                thread_id = thread_obj.get("id")
        except Exception as e:
            log_message(f"Failed to create thread on msg {msg_id}: {e}")

    if not thread_id:
        starter_banner = (
            f"🧵 **محادثة جديدة بدأها <@{author_id}>:** **{short_title}** (`{new_conv_id[:6]}`)"
            if is_ar
            else f"🧵 **New Session started by <@{author_id}>:** **{short_title}** (`{new_conv_id[:6]}`)"
        )
        try:
            starter = api_request(
                token,
                "POST",
                f"/channels/{general_channel_id}/messages",
                {"content": starter_banner}
            )
            starter_id = starter.get("id") if starter else None
            if starter_id:
                thread_obj = api_request(
                    token,
                    "POST",
                    f"/channels/{general_channel_id}/messages/{starter_id}/threads",
                    {"name": thread_name[:100], "auto_archive_duration": 10080}
                )
                if thread_obj and thread_obj.get("id"):
                    thread_id = thread_obj.get("id")
        except Exception as e:
            log_message(f"Failed to create thread fallback: {e}")

    if thread_id and author_id:
        try:
            api_request(token, "PUT", f"/channels/{thread_id}/thread-members/{author_id}")
        except Exception:
            pass

    return thread_id, thread_name, new_conv_id, starter_id


def parse_command_and_args(text):
    clean = text.strip()
    while clean and clean[0] in ("!", "/", "؟", "?", " "):
        clean = clean[1:].strip()
    if not clean:
        return "", ""
    parts = clean.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return cmd, arg


def handle_builtin_command(token, general_channel_id, cid, author_id, username, raw_content):
    """
    Checks if `raw_content` is a built-in management/status/history/session-control command.
    Automatically adapts output language (`en` vs `ar`) to match the user's command!
    """
    stripped = raw_content.strip()
    has_prefix = bool(stripped and stripped[0] in ("!", "/"))
    cmd, arg = parse_command_and_args(stripped)
    full_norm = f"{cmd} {arg}".strip().lower()
    lang = "ar" if discord_hook.is_arabic_text(stripped) else "en"

    # 1. Status Report
    if cmd in ("status", "stat", "state", "تقرير", "الحالة") and (not arg or full_norm in ("تقرير الحالة", "حالة النظام", "system status")):
        report = discord_status.get_status_report(lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled status command ({lang}) in {cid} from {username}")
        return True

    # 2. Crop Batch Report
    if cmd in ("crop", "قص", "القص") and (not arg or full_norm in ("دفعة القص", "حالة القص", "crop status")):
        report = discord_status.get_crop_report(lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled crop command ({lang}) in {cid} from {username}")
        return True

    # 2b. Phone Library Jobs Report
    if cmd in ("jobs", "job", "مهام", "المهام", "شغل") and (not arg or full_norm in ("مهام المكتبة", "phone jobs", "library jobs")):
        time_now = time.strftime("%H:%M:%S")
        jobs_body = discord_status.get_phone_jobs(lang=lang)
        header = f"📋 **مهام هاتف المكتبة النشطة (phone-library)** ({time_now})\n────────────────────────────\n" if lang == "ar" else f"📋 **Active phone-library Jobs** ({time_now})\n────────────────────────────\n"
        discord_hook.send_discord_rich(token, cid, header + jobs_body, is_user_prompt=False)
        log_message(f"Handled jobs command ({lang}) in {cid} from {username}")
        return True

    # 3. Help / Commands Guide
    if cmd in ("help", "commands", "أوامر", "الاوامر", "مساعدة", "دليل") and not arg:
        report = discord_manager.get_help_report(lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled help command ({lang}) in {cid} from {username}")
        return True

    # 4. Open / Launch Antigravity on Desktop
    if (
        (cmd in ("open", "launch", "start", "wake", "antig", "antigravity", "افتح", "تشغيل", "شغل") and (not arg or arg.lower() in ("open", "antig", "antigravity", "app", "ide", "التطبيق", "انتيغرافيتي")))
        or full_norm in ("open antigravity", "launch antigravity", "trigger antig open", "antig open", "open antig", "افتح التطبيق", "شغل التطبيق", "افتح انتيغرافيتي")
    ):
        try:
            api_request(token, "POST", f"/channels/{cid}/typing")
        except Exception:
            pass
        ok, ls_addr, csrf, started, msg = discord_manager.ensure_antigravity_open(wait_seconds=22, lang=lang)
        discord_hook.send_discord_rich(token, cid, msg, is_user_prompt=False)
        log_message(f"Handled open command ({lang}) in {cid} from {username}: ok={ok}, started={started}")
        return True

    # 5. List All Previous Conversations (Desktop + CLI)
    if cmd in ("chats", "convs", "conversations", "history", "list", "المحادثات", "السجل", "سجل", "محادثات") and (not arg or arg.isdigit() or arg.lower() in ("all", "الكل", "cli")):
        page = int(arg) if arg.isdigit() else 1
        src_filter = "cli" if arg.lower() == "cli" else None
        report = discord_manager.format_conversations_list_report(page=page, include_archived=True, source_filter=src_filter, lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled chats list command (page={page}, lang={lang}) in {cid} from {username}")
        return True

    # 5b. List CLI Conversations Only
    if cmd in ("cli-chats", "clichats") and (not arg or arg.isdigit()):
        page = int(arg) if arg.isdigit() else 1
        report = discord_manager.format_conversations_list_report(page=page, include_archived=True, source_filter="cli", lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled cli-chats command in {cid} from {username}")
        return True

    # 6. List Archived Conversations
    if cmd in ("archived", "archives", "المؤرشفة", "الارشيف", "الأرشيف", "مؤرشفة") or full_norm in ("المحادثات المؤرشفة", "archived chats"):
        page = int(arg) if arg.isdigit() else 1
        report = discord_manager.format_conversations_list_report(page=page, only_archived=True, lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled archived list command ({lang}) in {cid} from {username}")
        return True

    # 7. Search Previous Conversations
    if cmd in ("search", "find", "بحث", "ابحث") and arg:
        if has_prefix or cmd in ("search", "بحث"):
            report = discord_manager.format_search_report(arg, lang=lang)
            discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
            log_message(f"Handled search command ({arg}, lang={lang}) in {cid} from {username}")
            return True

    # 8. Read / Open Any Previous Conversation
    if cmd in ("read", "view", "show", "export", "اقرأ", "اقرا", "عرض", "استعراض"):
        if has_prefix or arg:
            try:
                api_request(token, "POST", f"/channels/{cid}/typing")
            except Exception:
                pass
            q_arg = arg
            if cmd == "export" and q_arg and "all" not in q_arg.lower():
                q_arg = f"{q_arg} export"
            reply = discord_manager.read_and_post_conversation_to_discord(
                token, general_channel_id, cid, author_id, q_arg, lang=lang
            )
            if reply:
                discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
            log_message(f"Handled read command ({q_arg}, lang={lang}) in {cid} from {username}")
            return True

    # 9. Archive / Unarchive Conversation
    if cmd in ("archive", "ارشف", "أرشف", "ارشفة", "أرشفة") and (has_prefix or arg or cid != general_channel_id):
        reply = discord_manager.toggle_archive_command(token, general_channel_id, cid, arg, archive=True, lang=lang)
        discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled archive command ({arg}, lang={lang}) in {cid} from {username}")
        return True

    if cmd in ("unarchive", "restore", "استعادة") or full_norm.startswith("الغاء ارشفة") or full_norm.startswith("إلغاء أرشفة"):
        clean_arg = arg
        for pfx in ("ارشفة", "أرشفة"):
            if clean_arg.startswith(pfx):
                clean_arg = clean_arg[len(pfx):].strip()
        reply = discord_manager.toggle_archive_command(token, general_channel_id, cid, clean_arg, archive=False, lang=lang)
        discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled unarchive command ({clean_arg}, lang={lang}) in {cid} from {username}")
        return True

    # 10. Live Sessions & Processes Control Center (!sessions / !running)
    if cmd in ("sessions", "running", "active", "الجلسات", "النشطة") and not arg:
        report = discord_manager.get_sessions_report(lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled sessions command ({lang}) in {cid} from {username}")
        return True

    # 11. AI Models & Quotas (!models / !quota)
    if cmd in ("models", "model", "quota", "quotas", "النماذج", "الحصة") and not arg:
        report = discord_manager.get_models_report(lang=lang)
        discord_hook.send_discord_rich(token, cid, report, is_user_prompt=False)
        log_message(f"Handled models command ({lang}) in {cid} from {username}")
        return True

    # 12. Stop / Cancel Running or Stuck Conversation (!stop / !cancel)
    if cmd in ("stop", "cancel", "abort", "ايقاف", "إيقاف") and (has_prefix or cid != general_channel_id):
        reply = discord_manager.stop_conversation_command(general_channel_id, cid, arg, lang=lang)
        discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled stop command ({arg}, lang={lang}) in {cid} from {username}")
        return True

    # 13. Fork Conversation (!fork)
    if cmd in ("fork", "branch", "clone", "نسخ", "فرع", "تفريع") and (has_prefix or cid != general_channel_id):
        reply = discord_manager.fork_conversation_command(token, general_channel_id, cid, author_id, arg, lang=lang)
        discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled fork command ({arg}, lang={lang}) in {cid} from {username}")
        return True

    # 14. Run or Resume via Headless Antigravity CLI (!cli / !agy)
    if cmd in ("cli", "agy") and has_prefix:
        reply = discord_manager.run_cli_prompt_command(token, general_channel_id, cid, author_id, arg, lang=lang)
        if reply:
            discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled cli command ({arg[:40]}, lang={lang}) in {cid} from {username}")
        return True

    # 15. Quick Shell Command Execution (!sh / !cmd)
    if cmd in ("sh", "cmd", "exec", "shell") and has_prefix:
        reply = discord_manager.run_shell_command(arg, lang=lang)
        discord_hook.send_discord_rich(token, cid, reply, is_user_prompt=False)
        log_message(f"Handled sh command ({arg[:40]}) in {cid} from {username}")
        return True

    return False


def main():
    conv_id = sys.argv[1] if len(sys.argv) > 1 else get_current_conv_id()
    is_agent_task = bool(os.environ.get("ANTIGRAVITY_AGENT") or len(sys.argv) > 1)

    config = load_json(CONFIG_FILE, {})
    token = config.get("bot_token")
    general_channel_id = config.get("channel_id")
    guild_id = config.get("guild_id")
    if not token or not general_channel_id:
        print("Error: Missing bot_token or channel_id in discord_config.json", file=sys.stderr)
        sys.exit(1)

    state = load_json(LISTENER_STATE_FILE, {})

    lock_file = os.path.join(CONFIG_DIR, "discord_listener.lock")
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                time.sleep(2)
    except Exception as e:
        log_message(f"Lock exception: {e}")

    log_message(f"Discord listener started (conv_id={conv_id}, is_agent_task={is_agent_task})")
    last_archive_sync = 0.0

    _err_streak = 0
    while True:
        try:
            now = time.time()
            # Periodically run two-way archive sync every 25 seconds
            if now - last_archive_sync >= 25.0:
                last_archive_sync = now
                try:
                    discord_manager.sync_archives(token, general_channel_id, guild_id)
                except Exception as sync_err:
                    log_message(f"Archive sync error: {sync_err}")

            threads = load_json(THREADS_FILE, {})
            channels_to_poll = [general_channel_id]
            if threads.get("active_thread_id") and threads["active_thread_id"] not in channels_to_poll:
                channels_to_poll.append(threads["active_thread_id"])
            for info in threads.values():
                if isinstance(info, dict) and info.get("thread_id"):
                    if info.get("archived") and info.get("thread_id") != threads.get("active_thread_id"):
                        continue
                    t_id = info["thread_id"]
                    if t_id not in channels_to_poll:
                        channels_to_poll.append(t_id)

            if guild_id and int(now) % 10 < 3:
                try:
                    act_res = api_request(token, "GET", f"/guilds/{guild_id}/threads/active")
                    for t_obj in (act_res or {}).get("threads", []):
                        if t_obj.get("parent_id") == general_channel_id:
                            tid = t_obj.get("id")
                            if tid and tid not in channels_to_poll:
                                channels_to_poll.append(tid)
                except Exception:
                    pass

            for cid in channels_to_poll:
                last_msg_id = state.get(cid)
                if not last_msg_id:
                    try:
                        msgs = api_request(token, "GET", f"/channels/{cid}/messages?limit=1")
                        if msgs and isinstance(msgs, list) and len(msgs) > 0:
                            state[cid] = msgs[0]["id"]
                            save_json(LISTENER_STATE_FILE, state)
                    except Exception as e:
                        log_message(f"Failed init state for {cid}: {e}")
                    continue

                query = f"/channels/{cid}/messages?limit=10&after={last_msg_id}"
                msgs = api_request(token, "GET", query)
                if msgs and isinstance(msgs, list) and len(msgs) > 0:
                    msgs_sorted = sorted(msgs, key=lambda m: int(m["id"]))
                    for m in msgs_sorted:
                        author = m.get("author", {})
                        if author.get("bot"):
                            state[cid] = m["id"]
                            save_json(LISTENER_STATE_FILE, state)
                            continue
                        if m.get("type", 0) not in (0, 19):
                            state[cid] = m["id"]
                            save_json(LISTENER_STATE_FILE, state)
                            continue

                        author_id = author.get("id")
                        content = m.get("content", "").strip()
                        attachments = m.get("attachments", [])

                        try:
                            att_urls = []
                            audio_local_paths = []
                            img_local_paths = []
                            other_local_paths = []

                            for a in attachments:
                                url = a.get("url")
                                if not url:
                                    continue
                                fname = a.get("filename", "")
                                ctype = a.get("content_type", "")
                                local_p, is_aud = download_attachment(url, fname)
                                if local_p:
                                    if is_aud or (ctype and ctype.startswith("audio/")):
                                        audio_local_paths.append(local_p)
                                    elif (ctype and ctype.startswith("image/")) or os.path.splitext(fname)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                                        img_local_paths.append((local_p, fname))
                                    else:
                                        other_local_paths.append((local_p, fname))
                                else:
                                    att_urls.append(url)

                            username = author.get("global_name") or author.get("username") or "User"
                            msg_lang = discord_hook.detect_language(content)
                            full_text = content
                            if img_local_paths:
                                for lp, fn in img_local_paths:
                                    lbl = "🖼️ **صورة مرفقة:**" if msg_lang == "ar" else "🖼️ **Attached Image:**"
                                    full_text = (full_text + ("\n" if full_text else "") + f"{lbl} [{fn}](file://{lp})").strip()
                            if audio_local_paths:
                                for lp in audio_local_paths:
                                    lbl = "🎙️ **تسجيل صوتي:** [تسجيل صوتي]" if msg_lang == "ar" else "🎙️ **Voice Message:** [voice]"
                                    full_text = (full_text + ("\n" if full_text else "") + f"{lbl}(file://{lp})").strip()
                            if other_local_paths:
                                for lp, fn in other_local_paths:
                                    lbl = "📎 **ملف مرفق:**" if msg_lang == "ar" else "📎 **Attached File:**"
                                    full_text = (full_text + ("\n" if full_text else "") + f"{lbl} [{fn}](file://{lp})").strip()
                            if att_urls:
                                full_text = (full_text + ("\n" if full_text else "") + "Attachments: " + ", ".join(att_urls)).strip()

                            threads = load_json(THREADS_FILE, {})
                            bot_id = "1559338113824985120"
                            clean_content = re.sub(rf"<@!?{bot_id}>\s*", "", content).strip()
                            has_any_media = bool(att_urls or audio_local_paths or img_local_paths or other_local_paths)

                            # Handle built-in commands
                            if clean_content and not has_any_media:
                                if handle_builtin_command(token, general_channel_id, cid, author_id, username, clean_content):
                                    state[cid] = m["id"]
                                    save_json(LISTENER_STATE_FILE, state)
                                    continue

                            if not clean_content and not has_any_media:
                                state[cid] = m["id"]
                                save_json(LISTENER_STATE_FILE, state)
                                continue

                            # ── Case A: Message posted directly in #general ──
                            if cid == general_channel_id:
                                thread_id, thread_name, new_conv_id, starter_id = create_thread_for_general_message(
                                    token, general_channel_id, m, author_id, clean_content,
                                    has_attachments=has_any_media
                                )
                                target_channel = thread_id or general_channel_id
                                try:
                                    api_request(token, "POST", f"/channels/{target_channel}/typing")
                                except Exception:
                                    pass

                                if thread_id:
                                    threads[new_conv_id] = {
                                        "thread_id": thread_id,
                                        "title": thread_name[:100],
                                        "starter_id": starter_id,
                                        "last_author_id": author_id,
                                    }
                                    threads["active_thread_id"] = thread_id
                                    save_json(THREADS_FILE, threads)

                                state[cid] = m["id"]
                                save_json(LISTENER_STATE_FILE, state)
                                log_message(f"Received prompt in #general from {username}, new thread {thread_name}")

                                if is_agent_task:
                                    print(f"[DISCORD_MESSAGE in {thread_name} (conv={new_conv_id}, thread_id={target_channel}, author_id={author_id}) from {username}]: {full_text}")
                                    sys.stdout.flush()
                                    sys.exit(0)

                                ok, ls_addr, csrf, was_started, open_msg = discord_manager.ensure_antigravity_open(wait_seconds=22, lang=msg_lang)
                                if was_started:
                                    discord_hook.send_discord_rich(token, target_channel, open_msg, is_user_prompt=False, ping_user=False)

                                d_ok, resolved_cid, err_msg = discord_manager.dispatch_prompt_via_agentapi(
                                    new_conv_id, target_channel, thread_name, author_id, username, full_text, is_new_thread=True
                                )
                                log_message(f"Dispatched #general prompt via agentapi: ok={d_ok}, cid={resolved_cid}, err={err_msg}")
                                if not d_ok:
                                    log_message("Falling back to headless agy CLI for #general prompt")
                                    try:
                                        import discord_hook as _dh
                                        _note = ("⚠️ تعذر الوصول إلى Antigravity Desktop بعد عدة محاولات؛ سيجيب `agy` CLI هذه المرة.\n> `" + str(err_msg)[:180] + "`"
                                                 if msg_lang == "ar" else
                                                 "⚠️ Antigravity Desktop rejected the prompt after retries — answering via `agy` CLI instead.\n> `" + str(err_msg)[:180] + "`")
                                        _dh.send_discord_rich(token, target_channel, _note, is_user_prompt=False, ping_user=False)
                                    except Exception:
                                        pass
                                    discord_manager.run_cli_prompt_command(token, general_channel_id, target_channel, author_id, full_text, lang=msg_lang)
                                continue

                            # ── Case B: Message posted inside an existing Thread ──
                            else:
                                try:
                                    api_request(token, "POST", f"/channels/{cid}/typing")
                                except Exception:
                                    pass

                                matched_conv = None
                                thread_label = cid
                                for c_key, c_info in threads.items():
                                    if isinstance(c_info, dict) and c_info.get("thread_id") == cid:
                                        matched_conv = c_key
                                        thread_label = c_info.get("title", cid)
                                        c_info["last_author_id"] = author_id
                                        if c_info.get("archived"):
                                            c_info["archived"] = False
                                            if len(c_key) >= 32:
                                                discord_manager.set_conv_archived_locally(c_key, False)
                                        break

                                threads["active_thread_id"] = cid
                                save_json(THREADS_FILE, threads)

                                state[cid] = m["id"]
                                save_json(LISTENER_STATE_FILE, state)
                                log_message(f"Received prompt in thread {thread_label} (conv={matched_conv}) from {username}")

                                if is_agent_task:
                                    # If the thread originates from an agy CLI session, route directly to agy instead of this desktop task
                                    if matched_conv and discord_manager.is_cli_session(matched_conv):
                                        log_message(f"Routing prompt in agy thread {thread_label} (conv={matched_conv}) directly to agy CLI")
                                        discord_manager.run_cli_prompt_command(
                                            token, general_channel_id, cid, author_id, full_text, lang=msg_lang
                                        )
                                        continue

                                    print(f"[DISCORD_MESSAGE in {thread_label} (conv={matched_conv or cid}, thread_id={cid}, author_id={author_id}) from {username}]: {full_text}")
                                    sys.stdout.flush()
                                    sys.exit(0)

                                # If thread is an agy CLI session, route directly to agy CLI
                                if matched_conv and discord_manager.is_cli_session(matched_conv):
                                    log_message(f"Routing prompt in agy thread {thread_label} (conv={matched_conv}) directly to agy CLI")
                                    discord_manager.run_cli_prompt_command(
                                        token, general_channel_id, cid, author_id, full_text, lang=msg_lang
                                    )
                                    continue

                                ok, ls_addr, csrf, was_started, open_msg = discord_manager.ensure_antigravity_open(wait_seconds=22, lang=msg_lang)
                                if was_started:
                                    discord_hook.send_discord_rich(token, cid, open_msg, is_user_prompt=False, ping_user=False)

                                d_ok, resolved_cid, err_msg = discord_manager.dispatch_prompt_via_agentapi(
                                    matched_conv, cid, thread_label, author_id, username, full_text, is_new_thread=False
                                )
                                log_message(f"Dispatched thread prompt via agentapi: ok={d_ok}, cid={resolved_cid}, err={err_msg}")
                                if not d_ok:
                                    log_message("Falling back to headless agy CLI for thread prompt")
                                    try:
                                        import discord_hook as _dh
                                        _note = ("⚠️ تعذر الوصول إلى Antigravity Desktop بعد عدة محاولات؛ سيجيب `agy` CLI هذه المرة.\n> `" + str(err_msg)[:180] + "`"
                                                 if msg_lang == "ar" else
                                                 "⚠️ Antigravity Desktop rejected the prompt after retries — answering via `agy` CLI instead.\n> `" + str(err_msg)[:180] + "`")
                                        _dh.send_discord_rich(token, cid, _note, is_user_prompt=False, ping_user=False)
                                    except Exception:
                                        pass
                                    discord_manager.run_cli_prompt_command(token, general_channel_id, cid, author_id, full_text, lang=msg_lang)
                                continue

                        except Exception as msg_err:
                            log_message(f"Error handling message {m.get('id')}: {msg_err}")
                            state[cid] = m["id"]
                            save_json(LISTENER_STATE_FILE, state)

            _err_streak = 0
        except Exception as loop_err:
            _err_streak += 1
            log_message(f"Error in listener loop (#{_err_streak}): {loop_err}")
            time.sleep(min(60, 2 ** min(_err_streak, 6)))  # backoff during Discord/network outages

        time.sleep(2.0)


if __name__ == "__main__":
    main()
