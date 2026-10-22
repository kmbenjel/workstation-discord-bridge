#!/usr/bin/env python3
import sys
import os
import re
import uuid
import json
import mimetypes
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

CONFIG_FILE = os.path.join(CONFIG_DIR, "discord_config.json")
STATE_FILE = os.path.join(CONFIG_DIR, "discord_state.json")
THREADS_FILE = os.path.join(CONFIG_DIR, "discord_threads.json")
LOCK_FILE = os.path.join(CONFIG_DIR, "discord_sync.lock")
BRAIN_DIR = os.environ.get("ANTIGRAVITY_BRAIN_DIR") or os.path.expanduser("~/.gemini/antigravity/brain")
CLI_BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity-cli/brain")

ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
STRONG_CHAR_RE = re.compile(r"([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]|[A-Za-z])")


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


def is_arabic_text(text):
    """Returns True if the prose of `text` (ignoring code spans, tags, and URLs) is primarily Arabic."""
    if not text:
        return False
    prose = re.sub(r"`[^`]*`", "", text)
    prose = re.sub(r"<[^>]+>", "", prose)
    prose = re.sub(r"https?://\S+", "", prose)
    ar_chars = ARABIC_CHAR_RE.findall(prose)
    if not ar_chars:
        return False
    en_chars = re.findall(r"[A-Za-z]", prose)
    m_first = STRONG_CHAR_RE.search(prose)
    if m_first and ARABIC_CHAR_RE.match(m_first.group(1)):
        return True
    return len(ar_chars) >= len(en_chars)


def detect_language(text):
    """Returns 'ar' if text is primarily Arabic, else 'en'."""
    return "ar" if is_arabic_text(text) else "en"


def api_request(token, method, path, payload=None):
    url = f"https://discord.com/api/v10{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://antigravity.google, 2.0)",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def upload_file_to_discord(token, target_channel_id, file_path):
    if not os.path.exists(file_path):
        return
    boundary = f"----AntigravityBoundary{uuid.uuid4().hex}"
    filename = os.path.basename(file_path)
    ctype, _ = mimetypes.guess_type(file_path)
    if not ctype:
        ctype = "application/octet-stream"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://discord.com/api/v10/channels/{target_channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "DiscordBot (https://antigravity.google, 2.0)",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except Exception:
        pass


def format_for_discord(md_text):
    """
    Language-adaptive Discord Markdown formatter:
    - Preserves 100% clean LTR formatting on English lines (no RLM/LRM injection, no broken backticks).
    - Applies RTL directionality and Bidi isolation ONLY on Arabic lines.
    - Never injects Unicode direction marks inside inline code (`...`) or fenced code blocks (```...```).
    - Only auto-attaches media/export files (images, PDFs, audio/video, or explicit discord_exports),
      never spamming source code files (.py, .sh, .md, .json) when linked in prose.
    """
    local_attachments = []

    def clean_local_path(p):
        clean_p = p.strip()
        if clean_p.startswith("file://"):
            clean_p = clean_p[7:]
        if "#" in clean_p:
            clean_p = clean_p.split("#", 1)[0]
        if os.name == "nt" and clean_p.startswith("/") and len(clean_p) > 2 and clean_p[2] == ":":
            clean_p = clean_p.lstrip("/")
        return os.path.normpath(clean_p)

    def replace_img(match):
        alt = match.group(1).strip()
        clean_p = clean_local_path(match.group(2))
        if os.path.exists(clean_p) and os.path.isfile(clean_p):
            if os.path.getsize(clean_p) <= 20 * 1024 * 1024:
                if clean_p not in local_attachments and len(local_attachments) < 10:
                    local_attachments.append(clean_p)
                return f"🖼️ **{alt or os.path.basename(clean_p)}**"
        return match.group(0)

    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_img, md_text)

    def replace_file_link(match):
        label = match.group(1).strip()
        clean_p = clean_local_path(match.group(2))
        clean_label = re.sub(r"</?bdi[^>]*>", "", label, flags=re.IGNORECASE).strip("`")
        if os.path.exists(clean_p) and os.path.isfile(clean_p):
            ext = os.path.splitext(clean_p)[1].lower()
            is_explicit_export = "discord_exports" in clean_p
            is_media = ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp3", ".mp4"}
            if os.path.getsize(clean_p) <= 20 * 1024 * 1024 and (is_media or is_explicit_export):
                if clean_p not in local_attachments and len(local_attachments) < 10:
                    local_attachments.append(clean_p)
                icon = "🖼️" if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"} else "📎"
                return f"{icon} `{clean_label}`"
        return f"`{clean_label}`"

    text = re.sub(r"\[([^\]]+)\]\((file:///[^)]+)\)", replace_file_link, text)
    text = re.sub(r"\[([^\]]+)\]\(conversation://[^)]+\)", r"💬 `\1`", text)

    # First, strip any <bdi> tags that were accidentally placed INSIDE inline code spans `...`
    def strip_bdi_in_code(match):
        inner = match.group(1)
        inner = re.sub(r"</?bdi[^>]*>", "", inner, flags=re.IGNORECASE)
        return f"`{inner}`"

    text = re.sub(r"`([^`\n]+)`", strip_bdi_in_code, text)

    # Strip container HTML tags
    text = re.sub(r"</?div[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?span[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # Discord markdown only supports headings up to level 3 (###).
    text = re.sub(r"^#{4,}\s*", "### ", text, flags=re.MULTILINE)

    lines = text.split("\n")
    in_code = False
    out_lines = []
    rlm = "\u200F"
    lrm = "\u200E"

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out_lines.append(line)
            continue
        if in_code:
            out_lines.append(line)
            continue

        if re.match(r"^\s*---+\s*$", line):
            out_lines.append("────────────────────────────")
            continue

        if is_arabic_text(line):
            # Convert <bdi> tags on Arabic lines to LRM...RLM isolates
            line = re.sub(
                r"<bdi[^>]*>(.*?)</bdi>",
                lambda m: f"{lrm}{m.group(1).strip()}{rlm}",
                line,
                flags=re.DOTALL | re.IGNORECASE,
            )
            line = re.sub(r"([\u0600-\u06FF]),", r"\1،", line)
            line = re.sub(r",([\u0600-\u06FF])", r"، \1", line)
            if line.endswith("?"):
                line = line[:-1] + "؟"

            marker_match = re.match(
                r"^(\s*(?:[-•]|\*(?!\*))\s+|\s*\d+\.\s+|\s*#{1,3}\s+|\s*>\s*)(.*)",
                line,
            )
            if marker_match:
                prefix = marker_match.group(1)
                rest = marker_match.group(2)
                line = f"{prefix}{rlm}{rest.strip()}"
            else:
                line = f"{rlm}{line}"

            # Isolate inline code spans on Arabic lines without touching content inside backticks
            line = re.sub(r"(`[^`]+`)", rf"{lrm}\1{rlm}", line)
        else:
            # Pure English / LTR line: strip any stray <bdi> tags cleanly with zero invisible marks
            line = re.sub(r"</?bdi[^>]*>", "", line, flags=re.IGNORECASE)

        out_lines.append(line)

    return "\n".join(out_lines).strip(), local_attachments


def split_markdown_chunks(text, max_len=1900):
    """Splits markdown cleanly without breaking fenced code blocks."""
    chunks = []
    lines = text.split("\n")
    cur_lines = []
    cur_len = 0
    in_code_block = False
    code_fence_header = "```"

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_fence_header = stripped
            else:
                in_code_block = False

        line_len = len(line) + 1
        if cur_len + line_len > max_len and cur_lines:
            if in_code_block:
                cur_lines.append("```")
                chunks.append("\n".join(cur_lines))
                cur_lines = [code_fence_header, line]
                cur_len = len(code_fence_header) + 1 + line_len
            else:
                chunks.append("\n".join(cur_lines))
                cur_lines = [line]
                cur_len = line_len
        else:
            cur_lines.append(line)
            cur_len += line_len

    if cur_lines:
        chunks.append("\n".join(cur_lines))
    return chunks


import hashlib
import time

_RECENT_MSGS = {}  # (target_channel_id, md5): timestamp


def send_discord_rich(token, target_channel_id, text, is_user_prompt=False, ping_user=True):
    if not text or not text.strip():
        return

    # Deduplication: prevent sending exact duplicate messages to the same channel within 25 seconds
    msg_hash = hashlib.md5(text.strip().encode('utf-8', errors='ignore')).hexdigest()
    cache_key = (str(target_channel_id), msg_hash)
    now = time.time()
    # Clean old cache entries
    for k in list(_RECENT_MSGS.keys()):
        if now - _RECENT_MSGS[k] > 60:
            _RECENT_MSGS.pop(k, None)
    if cache_key in _RECENT_MSGS and (now - _RECENT_MSGS[cache_key]) < 25:
        return
    _RECENT_MSGS[cache_key] = now

    if is_user_prompt:
        embed = {
            "author": {"name": "👤 You"},
            "description": text[:4000],
            "color": 0x5865F2,
        }
        try:
            api_request(
                token,
                "POST",
                f"/channels/{target_channel_id}/messages",
                {"embeds": [embed]},
            )
        except Exception:
            pass
        return

    formatted_text, local_attachments = format_for_discord(text)
    config = load_json(CONFIG_FILE, {})
    threads = load_json(THREADS_FILE, {})

    target_user_id = None
    if ping_user:
        for info in threads.values():
            if isinstance(info, dict) and info.get("thread_id") == target_channel_id:
                target_user_id = info.get("last_author_id")
                break
        if not target_user_id:
            target_user_id = config.get("user_id")

    if target_user_id and ping_user:
        formatted_text = f"<@{target_user_id}>\n{formatted_text}"
    chunks = split_markdown_chunks(formatted_text, max_len=1900)

    for chunk in chunks:
        try:
            api_request(
                token,
                "POST",
                f"/channels/{target_channel_id}/messages",
                {"content": chunk},
            )
        except Exception:
            pass

    for f_path in local_attachments:
        upload_file_to_discord(token, target_channel_id, f_path)


def extract_user_request(raw_content):
    if not raw_content:
        return ""
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw_content, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw_content.strip()


def get_or_create_thread(token, channel_id, conv_id, steps, custom_title=None):
    threads = load_json(THREADS_FILE, {})

    # 1. Exact conversation match ALWAYS takes priority so each conversation maps to its own thread!
    if conv_id and conv_id in threads and isinstance(threads[conv_id], dict) and threads[conv_id].get("thread_id"):
        return threads[conv_id]["thread_id"]

    # 2. If the conversation ORIGINATED from Discord (its first prompt is [DISCORD_MESSAGE]), bind to its thread
    first_user_step = None
    for s in steps:
        if s.get("type") == "USER_INPUT":
            first_user_step = s
            break

    if first_user_step:
        raw_first = first_user_step.get("content", "")
        if "[DISCORD_MESSAGE" in raw_first:
            m_tid = re.search(r"thread_id=(\d+)", raw_first)
            if m_tid:
                tid = m_tid.group(1)
                threads[conv_id] = {
                    "thread_id": tid,
                    "title": custom_title or f"💬 {conv_id[:8]}",
                }
                save_json(THREADS_FILE, threads)
                return tid
            m_conv = re.search(r"\(conv=([a-f0-9]+),", raw_first)
            if m_conv:
                short_key = m_conv.group(1)
                if short_key in threads and isinstance(threads[short_key], dict) and threads[short_key].get("thread_id"):
                    info = threads.pop(short_key)
                    threads[conv_id] = info
                    save_json(THREADS_FILE, threads)
                    return info["thread_id"]

    # 3. Create a dedicated thread for this desktop/new conversation
    first_prompt = custom_title or "Antigravity Session"
    if not custom_title:
        for s in steps:
            if s.get("type") == "USER_INPUT":
                extracted = extract_user_request(s.get("content", ""))
                if extracted:
                    first_prompt = extracted.replace("\n", " ").strip()
                    break

    clean_title = first_prompt[:65].strip()
    thread_name = f"💬 {clean_title}"
    short_id = (conv_id or "session")[:8]

    config = load_json(CONFIG_FILE, {})
    user_id = config.get("user_id")

    if is_arabic_text(clean_title):
        starter_text = f"🧵 **محادثة جديدة (`{short_id}`):** **{clean_title}**"
    else:
        starter_text = f"🧵 **New Session (`{short_id}`):** **{clean_title}**"

    try:
        starter = api_request(
            token,
            "POST",
            f"/channels/{channel_id}/messages",
            {"content": starter_text},
        )
        starter_id = starter.get("id")
        if starter_id:
            thread_obj = api_request(
                token,
                "POST",
                f"/channels/{channel_id}/messages/{starter_id}/threads",
                {"name": thread_name[:100], "auto_archive_duration": 10080},
            )
            thread_id = thread_obj.get("id")
            if thread_id:
                if user_id:
                    try:
                        api_request(
                            token,
                            "PUT",
                            f"/channels/{thread_id}/thread-members/{user_id}",
                        )
                    except Exception:
                        pass
                threads[conv_id] = {
                    "thread_id": thread_id,
                    "title": thread_name[:100],
                    "starter_id": starter_id,
                    "last_author_id": user_id,
                }
                threads["active_thread_id"] = thread_id
                save_json(THREADS_FILE, threads)
                return thread_id
    except Exception:
        pass

    if threads.get("active_thread_id"):
        return threads["active_thread_id"]
    return channel_id


def find_transcript_path(payload):
    conv_id = payload.get("conversationId", "")
    if conv_id:
        for b_dir in (BRAIN_DIR, CLI_BRAIN_DIR):
            full_p = os.path.join(b_dir, conv_id, ".system_generated", "logs", "transcript_full.jsonl")
            if os.path.exists(full_p):
                return full_p
            comp_p = os.path.join(b_dir, conv_id, ".system_generated", "logs", "transcript.jsonl")
            if os.path.exists(comp_p):
                return comp_p
    tp = payload.get("transcriptPath", "")
    if tp and os.path.exists(tp):
        return tp
    return None


def sync_conversation(token, channel_id, conv_id, t_path):
    if not t_path or not os.path.exists(t_path):
        return

    lock_fd = None
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    except Exception:
        lock_fd = None

    try:
        state = load_json(STATE_FILE, {})
        last_sent_idx = state.get(conv_id, -1)

        steps = []
        last_turn_idx = -1
        last_user_prompt = None
        last_user_step_idx = -1

        try:
            with open(t_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        step = json.loads(line)
                        steps.append(step)
                        s_idx = step.get("step_index", -1)
                        if step.get("type") in ("USER_INPUT", "SYSTEM_MESSAGE"):
                            if s_idx > last_turn_idx:
                                last_turn_idx = s_idx
                        if step.get("type") == "USER_INPUT":
                            if s_idx > last_user_step_idx:
                                last_user_step_idx = s_idx
                                last_user_prompt = extract_user_request(step.get("content", ""))
                    except Exception:
                        continue
        except Exception:
            return

        cutoff_idx = last_sent_idx
        latest_step_idx = last_sent_idx
        items_to_send = []

        # Do not echo user prompt back to Discord if the prompt itself originated from Discord
        if last_user_step_idx > last_sent_idx and last_user_prompt:
            if not last_user_prompt.startswith("[DISCORD_MESSAGE"):
                items_to_send.append((last_user_step_idx, last_user_prompt, True))
            else:
                if last_user_step_idx > latest_step_idx:
                    latest_step_idx = last_user_step_idx

        for step in steps:
            s_idx = step.get("step_index", -1)
            if (
                step.get("source") == "MODEL"
                and step.get("type") == "PLANNER_RESPONSE"
                and s_idx > cutoff_idx
            ):
                content = step.get("content", "")
                if content and content.strip():
                    items_to_send.append((s_idx, content.strip(), False))

        if items_to_send:
            target_thread_id = get_or_create_thread(token, channel_id, conv_id, steps)
            for s_idx, msg, is_user in items_to_send:
                send_discord_rich(token, target_thread_id, msg, is_user_prompt=is_user)
                if s_idx > latest_step_idx:
                    latest_step_idx = s_idx

        if latest_step_idx > last_sent_idx:
            state[conv_id] = latest_step_idx
            save_json(STATE_FILE, state)
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except Exception:
                pass


def main():
    raw_in = sys.stdin.read()
    payload = {}
    if raw_in.strip():
        try:
            payload = json.loads(raw_in)
        except Exception:
            pass

    config = load_json(CONFIG_FILE, {})
    token = config.get("bot_token")
    channel_id = config.get("channel_id")

    if token and channel_id:
        t_path = find_transcript_path(payload)
        conv_id = payload.get("conversationId", "default")
        sync_conversation(token, channel_id, conv_id, t_path)

    if "invocationNum" in payload:
        sys.stdout.write(json.dumps({}))
    else:
        sys.stdout.write(json.dumps({"decision": "allow"}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
