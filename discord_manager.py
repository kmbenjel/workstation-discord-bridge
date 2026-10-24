#!/usr/bin/env python3
"""
Discord Manager & Max-Power Session Controller for Antigravity (Desktop & CLI):
1. Discovers running Antigravity Language Server (port + CSRF token) and auto-launches Antigravity Desktop if closed.
2. Unifies Desktop (~/.gemini/antigravity) and CLI (~/.gemini/antigravity-cli) conversation stores with automatic bi-directional symlinking.
3. Lists, searches, reads, and exports all previous conversations (active & archived, Desktop & CLI) in either English or Arabic layout.
4. Bi-directionally syncs archived status between Antigravity (annotations/*.pbtxt) and Discord threads.
5. Provides Max-Power Session Control from Discord:
   - Dispatch to Desktop via `agentapi` (or auto-launch Desktop if closed).
   - Dispatch to headless CLI via `/home/k/.local/bin/agy` (`!cli` / `!agy`) for any existing or new session.
   - Live session & background process inspector (`!sessions` / `!running`).
   - Live AI model & quota inspector (`!models` / `!quota`).
   - Stop/cancel running or stuck cascades (`!stop` / `!cancel`).
   - Fork any conversation into a new branch thread (`!fork`).
   - Quick shell command execution (`!sh` / `!cmd`).
"""

import os
import re
import sys
import glob
import time
import json
import shutil
import sqlite3
import subprocess
import urllib.request
import urllib.error

CONFIG_DIR = os.environ.get("GEMINI_CONFIG_DIR") or os.path.expanduser("~/.gemini/config")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

CONFIG_FILE = os.path.join(CONFIG_DIR, "discord_config.json")
THREADS_FILE = os.path.join(CONFIG_DIR, "discord_threads.json")
STATE_FILE = os.path.join(CONFIG_DIR, "discord_state.json")
ARCHIVE_STATE_FILE = os.path.join(CONFIG_DIR, "discord_archive_state.json")

AGY_DIR = os.environ.get("ANTIGRAVITY_APP_DATA_DIR") or os.path.expanduser("~/.gemini/antigravity")
BRAIN_DIR = os.environ.get("ANTIGRAVITY_BRAIN_DIR") or os.path.join(AGY_DIR, "brain")
CONV_DIR = os.path.join(AGY_DIR, "conversations")
ANN_DIR = os.path.join(AGY_DIR, "annotations")
DB_PATH = os.path.join(AGY_DIR, "conversation_summaries.db")

CLI_DIR = os.path.expanduser("~/.gemini/antigravity-cli")
CLI_BRAIN_DIR = os.path.join(CLI_DIR, "brain")
CLI_CONV_DIR = os.path.join(CLI_DIR, "conversations")
CLI_ANN_DIR = os.path.join(CLI_DIR, "annotations")
CLI_DB_PATH = os.path.join(CLI_DIR, "conversation_summaries.db")

AGENTAPI_BIN = "/opt/Antigravity/resources/bin/language_server"
ANTIGRAVITY_BIN = "/opt/Antigravity/antigravity"
AGY_CLI_BIN = "/home/k/.local/bin/agy"


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


def discord_api(token, method, path, payload=None):
    url = f"https://discord.com/api/v10{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "DiscordBot (https://antigravity.google, 2.0)",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Antigravity Process & Language Server Discovery / Auto-Launcher
# ─────────────────────────────────────────────────────────────────────────────

def is_antigravity_running():
    try:
        res = subprocess.run(["pgrep", "-f", "/opt/Antigravity/antigravity"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False


def discover_language_server():
    """Returns (ls_address, csrf_token, pid) for the active Antigravity language_server, or (None, None, None)."""
    try:
        out = subprocess.check_output(["pgrep", "-f", "resources/bin/language_server"], stderr=subprocess.DEVNULL).decode()
        pids = [p.strip() for p in out.split() if p.strip()]
    except Exception:
        return None, None, None

    try:
        ss_out = subprocess.check_output(["ss", "-tulpn"], stderr=subprocess.DEVNULL).decode()
    except Exception:
        ss_out = ""

    for pid in pids:
        try:
            cmdline_raw = open(f"/proc/{pid}/cmdline", "rb").read().decode("utf-8", errors="ignore")
            cmdline = [c for c in cmdline_raw.split("\x00") if c]
            if "--csrf_token" not in cmdline:
                continue
            csrf = cmdline[cmdline.index("--csrf_token") + 1]
            ports = re.findall(rf"127\.0\.0\.1:(\d+).*?pid={pid},", ss_out)
            for p in ports:
                url = f"http://127.0.0.1:{p}/exa.language_server_pb.LanguageServerService/GetStatus"
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=1.5) as resp:
                        if resp.status == 200:
                            return f"localhost:{p}", csrf, int(pid)
                except Exception:
                    continue
        except Exception:
            continue
    return None, None, None


def get_graphical_env():
    """Builds an environment dict with DISPLAY, WAYLAND_DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS."""
    env = os.environ.copy()
    uid = os.getuid()
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("USER", os.environ.get("USER", "k"))
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")

    try:
        sys_env = subprocess.check_output(["systemctl", "--user", "show-environment"], stderr=subprocess.DEVNULL).decode()
        for line in sys_env.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "PATH"):
                    env[k] = v
    except Exception:
        pass

    env.setdefault("DISPLAY", ":0")
    if os.path.exists(f"/run/user/{uid}/wayland-0"):
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    return env


def ensure_antigravity_open(wait_seconds=22, lang="en"):
    """
    Checks if Antigravity and its Language Server are running.
    If not, launches `/opt/Antigravity/antigravity --disable-gpu` on the user's desktop
    and waits up to `wait_seconds` for the Language Server to become ready.
    Returns (success: bool, ls_addr: str|None, csrf: str|None, was_started: bool, message: str).
    """
    ls_addr, csrf, pid = discover_language_server()
    if ls_addr and csrf and is_antigravity_running():
        if lang == "ar":
            msg = f"🖥️ **تطبيق Antigravity مفتوح ويعمل بالفعل** (PID `{pid}`، المنفذ `{ls_addr}`)."
        else:
            msg = f"🖥️ **Antigravity Desktop is already open and running** (PID `{pid}`, address `{ls_addr}`)."
        return True, ls_addr, csrf, False, msg

    env = get_graphical_env()
    try:
        subprocess.Popen(
            [ANTIGRAVITY_BIN, "--disable-gpu"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        if lang == "ar":
            return False, None, None, False, f"⚠️ **تعذر تشغيل تطبيق Antigravity:** `{e}`"
        return False, None, None, False, f"⚠️ **Failed to launch Antigravity Desktop:** `{e}`"

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(1.0)
        ls_addr, csrf, pid = discover_language_server()
        if ls_addr and csrf:
            if lang == "ar":
                msg = f"🚀 **تم فتح تطبيق Antigravity على سطح المكتب وتشغيل خادم اللغة بنجاح** (PID `{pid}`، المنفذ `{ls_addr}`)."
            else:
                msg = f"🚀 **Launched Antigravity Desktop and connected to Language Server** (PID `{pid}`, address `{ls_addr}`)."
            return True, ls_addr, csrf, True, msg

    if lang == "ar":
        return False, None, None, True, "⏳ **تم إطلاق عملية Antigravity على سطح المكتب، وجارٍ تهيئة خادم اللغة...**"
    return False, None, None, True, "⏳ **Antigravity Desktop process launched; Language Server is still initializing...**"


def ls_rpc(method, payload, ls_addr=None, csrf=None, timeout=10):
    if not ls_addr or not csrf:
        ls_addr, csrf, _ = discover_language_server()
    if not ls_addr or not csrf:
        return None
    url = f"http://{ls_addr}/exa.language_server_pb.LanguageServerService/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": csrf},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        return None


def _call_agentapi_once(args, timeout=25):
    ok, ls_addr, csrf, _, _ = ensure_antigravity_open(wait_seconds=22)
    if not ok or not ls_addr or not csrf:
        return False, {"error": "Language server unavailable"}

    env = os.environ.copy()
    env["ANTIGRAVITY_LS_ADDRESS"] = ls_addr
    env["ANTIGRAVITY_CSRF_TOKEN"] = csrf
    # Without a project id the LS rejects new conversations with
    # "project_id is required when providing project_env_config" (systemd env lacks it).
    env.setdefault("ANTIGRAVITY_PROJECT_ID", "outside-of-project")
    cmd = [AGENTAPI_BIN, "agentapi"] + list(args)
    try:
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        out = res.stdout.strip()
        try:
            data = json.loads(out) if out else {}
        except ValueError:
            data = {"error": out[:500]}
        if res.returncode == 0 and not data.get("error"):
            return True, data
        return False, data or {"error": res.stderr.strip() or f"Exit code {res.returncode}"}
    except Exception as e:
        return False, {"error": str(e)}


def call_agentapi(args, timeout=25, retries=3):
    """Calls `/opt/Antigravity/resources/bin/language_server agentapi <args>` with discovered LS credentials.

    Retries with backoff: the LS transiently rejects requests (e.g. `project_id is required when
    providing project_env_config`, connection refused) while Antigravity restarts/auto-updates.
    """
    delays = [3, 8, 15]
    ok, data = False, {}
    for attempt in range(max(1, retries)):
        ok, data = _call_agentapi_once(args, timeout=timeout)
        if ok:
            return ok, data
        if attempt < retries - 1:
            time.sleep(delays[min(attempt, len(delays) - 1)])
    return ok, data


# ─────────────────────────────────────────────────────────────────────────────
# 2. Unified Desktop + CLI Conversation Store & Search
# ─────────────────────────────────────────────────────────────────────────────

def sync_cli_and_desktop_stores(target_cid=None):
    """
    Ensures CLI (`~/.gemini/antigravity-cli`) and Desktop (`~/.gemini/antigravity`) conversation files,
    brain directories, and annotations are cross-linked so any CLI session can be opened in Desktop/Discord
    and any Desktop session can be resumed via `agy --conversation <id>`.
    """
    try:
        # 1. Link CLI -> Desktop for any CLI conversation missing in Desktop
        for src_dir, dst_dir in (
            (CLI_CONV_DIR, CONV_DIR),
            (CLI_BRAIN_DIR, BRAIN_DIR),
            (CLI_ANN_DIR, ANN_DIR),
        ):
            if os.path.isdir(src_dir) and os.path.isdir(dst_dir):
                for name in os.listdir(src_dir):
                    s_path = os.path.join(src_dir, name)
                    d_path = os.path.join(dst_dir, name)
                    if not os.path.exists(d_path) and not os.path.islink(d_path):
                        try:
                            os.symlink(s_path, d_path)
                        except Exception:
                            pass

        # 2. If `target_cid` is specified (e.g. before running `agy --conversation <cid>`), link Desktop -> CLI
        if target_cid:
            for ext in (".pb", ".db"):
                s_conv = os.path.join(CONV_DIR, f"{target_cid}{ext}")
                d_conv = os.path.join(CLI_CONV_DIR, f"{target_cid}{ext}")
                if os.path.exists(s_conv) and not os.path.exists(d_conv) and not os.path.islink(d_conv):
                    os.makedirs(CLI_CONV_DIR, exist_ok=True)
                    try:
                        os.symlink(s_conv, d_conv)
                    except Exception:
                        pass
            s_brain = os.path.join(BRAIN_DIR, target_cid)
            d_brain = os.path.join(CLI_BRAIN_DIR, target_cid)
            if os.path.exists(s_brain) and not os.path.exists(d_brain) and not os.path.islink(d_brain):
                os.makedirs(CLI_BRAIN_DIR, exist_ok=True)
                try:
                    os.symlink(s_brain, d_brain)
                except Exception:
                    pass
    except Exception:
        pass


def is_conv_archived_locally(cid):
    for ann_dir in (ANN_DIR, CLI_ANN_DIR):
        ann_p = os.path.join(ann_dir, f"{cid}.pbtxt")
        if os.path.exists(ann_p):
            try:
                txt = open(ann_p, "r", encoding="utf-8", errors="ignore").read()
                if re.search(r"\barchived\s*:\s*true\b", txt):
                    return True
            except Exception:
                pass
    return False


def get_annotation_title(cid):
    for ann_dir in (ANN_DIR, CLI_ANN_DIR):
        ann_p = os.path.join(ann_dir, f"{cid}.pbtxt")
        if os.path.exists(ann_p):
            try:
                txt = open(ann_p, "r", encoding="utf-8", errors="ignore").read()
                m = re.search(r'title\s*:\s*"([^"]+)"', txt)
                if m:
                    return m.group(1).strip()
            except Exception:
                pass
    return ""


def set_conv_archived_locally(cid, archived=True):
    """Updates Antigravity's annotation for `cid` via LanguageServer RPC and direct pbtxt update."""
    res = ls_rpc("UpdateConversationAnnotations", {
        "cascadeId": cid,
        "annotations": {"archived": bool(archived)},
        "mergeAnnotations": True,
    })
    for ann_dir in (ANN_DIR, CLI_ANN_DIR):
        if not os.path.isdir(ann_dir):
            continue
        ann_p = os.path.join(ann_dir, f"{cid}.pbtxt")
        if ann_dir == CLI_ANN_DIR and not os.path.exists(ann_p):
            continue
        try:
            txt = open(ann_p, "r", encoding="utf-8", errors="ignore").read() if os.path.exists(ann_p) else ""
            now_sec = int(time.time())
            if archived:
                if not re.search(r"\barchived\s*:\s*true\b", txt):
                    txt = re.sub(r"\barchived\s*:\s*false\b", "", txt).strip()
                    txt = f"archived:true archival_status_timestamp:{{seconds:{now_sec} nanos:0}} {txt}".strip()
                    with open(ann_p, "w", encoding="utf-8") as f:
                        f.write(txt + "\n")
            else:
                if re.search(r"\barchived\s*:\s*true\b", txt):
                    txt = re.sub(r"\barchived\s*:\s*true\b", "", txt)
                    txt = re.sub(r"archival_status_timestamp\s*:\s*\{[^}]*\}", "", txt).strip()
                    with open(ann_p, "w", encoding="utf-8") as f:
                        f.write(txt + "\n")
        except Exception:
            pass
    return res is not None


def has_local_conversation_data(cid):
    return any(os.path.exists(p) for p in (
        os.path.join(BRAIN_DIR, cid, ".system_generated", "logs", "transcript_full.jsonl"),
        os.path.join(BRAIN_DIR, cid, ".system_generated", "logs", "transcript.jsonl"),
        os.path.join(CONV_DIR, f"{cid}.db"),
        os.path.join(CONV_DIR, f"{cid}.pb"),
        os.path.join(CLI_BRAIN_DIR, cid, ".system_generated", "logs", "transcript_full.jsonl"),
        os.path.join(CLI_BRAIN_DIR, cid, ".system_generated", "logs", "transcript.jsonl"),
        os.path.join(CLI_CONV_DIR, f"{cid}.db"),
        os.path.join(CLI_CONV_DIR, f"{cid}.pb"),
    ))


def detect_conversation_source(cid, in_desktop_db=False, in_cli_db=False):
    desk_pb = os.path.join(CONV_DIR, f"{cid}.pb")
    desk_db = os.path.join(CONV_DIR, f"{cid}.db")
    desk_brain = os.path.join(BRAIN_DIR, cid)
    is_symlinked_from_cli = any(os.path.islink(p) for p in (desk_pb, desk_db, desk_brain))
    if is_symlinked_from_cli or (in_cli_db and not in_desktop_db):
        return "CLI"
    cli_brain = os.path.join(CLI_BRAIN_DIR, cid)
    if os.path.exists(cli_brain) and (not os.path.exists(desk_brain) or os.path.islink(desk_brain)):
        return "CLI"
    return "Desktop"


def is_cli_session(cid):
    if not cid:
        return False
    return detect_conversation_source(cid) == "CLI"


def get_conversation_project_dir(cid):
    """Inspects transcript logs to detect the working directory of a conversation (e.g. ~/projects/career-ops)."""
    if not cid:
        return os.path.expanduser("~")
    for base in [CLI_BRAIN_DIR, BRAIN_DIR]:
        p = os.path.join(base, cid, ".system_generated", "logs", "transcript.jsonl")
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(250000)
                    matches = re.findall(r"(/home/k/projects/[a-zA-Z0-9_\-]+)", content)
                    for m in matches:
                        if os.path.isdir(m):
                            return m
            except Exception:
                pass
    return os.path.expanduser("~")


def list_all_conversations(include_archived=True, only_archived=False, source_filter=None):
    """
    Returns a unified list of dicts for all root conversations across Desktop and CLI sorted by last_modified_time DESC:
    [{"index": 1, "id": cid, "short_id": cid[:8], "title": ..., "steps": ..., "modified": ...,
      "archived": ..., "status": ..., "source": "Desktop"|"CLI", "thread_id": ...}]
    """
    sync_cli_and_desktop_stores()
    threads = load_json(THREADS_FILE, {})
    conv_to_thread = {}
    for k, v in threads.items():
        if isinstance(v, dict) and v.get("thread_id"):
            conv_to_thread[k] = v["thread_id"]

    merged = {}
    desktop_ids = set()
    cli_ids = set()

    for db_file, is_cli in ((DB_PATH, False), (CLI_DB_PATH, True)):
        if not os.path.exists(db_file):
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT conversation_id, title, preview, step_count, last_modified_time, status
                FROM conversation_summaries
                WHERE nesting_depth = 0
                ORDER BY last_modified_time DESC
            """).fetchall()
            conn.close()
            for cid, title, preview, sc, lmt, status in rows:
                if is_cli:
                    cli_ids.add(cid)
                else:
                    desktop_ids.add(cid)
                lmt_str = str(lmt or "")[:19]
                if cid not in merged or lmt_str > merged[cid]["raw_lmt"]:
                    merged[cid] = {
                        "id": cid,
                        "title": title,
                        "preview": preview,
                        "steps": sc or 0,
                        "raw_lmt": lmt_str,
                        "status": status or "",
                    }
        except Exception:
            pass

    sorted_rows = sorted(merged.values(), key=lambda x: x["raw_lmt"], reverse=True)
    results = []

    for row in sorted_rows:
        cid = row["id"]
        if not has_local_conversation_data(cid):
            continue
        is_arch = is_conv_archived_locally(cid)
        if only_archived and not is_arch:
            continue
        if not include_archived and is_arch:
            continue

        src = detect_conversation_source(cid, cid in desktop_ids, cid in cli_ids)
        if source_filter and source_filter.lower() not in src.lower():
            continue

        ann_title = get_annotation_title(cid)
        disp_title = (ann_title or row["title"] or row["preview"] or cid[:8]).replace("\n", " ").strip()
        if len(disp_title) > 72:
            disp_title = disp_title[:69] + "..."

        results.append({
            "id": cid,
            "short_id": cid[:8],
            "title": disp_title,
            "steps": row["steps"],
            "modified": row["raw_lmt"][:16].replace("T", " "),
            "archived": is_arch,
            "status": row["status"],
            "source": src,
            "thread_id": conv_to_thread.get(cid),
        })

    for i, item in enumerate(results, 1):
        item["index"] = i
    return results


def resolve_conversation_query(query_str, only_archived=False):
    """
    Resolves a conversation by:
    - 1-based index from `list_all_conversations(include_archived=True)` (e.g., "1", "3", "#5")
    - Exact or prefix UUID match (e.g., "068a0a41", "52536794-00f8...")
    - Substring match in title
    """
    q = query_str.strip().lstrip("#")
    all_convs = list_all_conversations(include_archived=True, only_archived=only_archived)
    if not q:
        return all_convs[0] if all_convs else None

    if q.isdigit():
        idx = int(q)
        if 1 <= idx <= len(all_convs):
            return all_convs[idx - 1]

    q_low = q.lower()
    for c in all_convs:
        if c["id"].lower() == q_low or c["id"].lower().startswith(q_low):
            return c

    for c in all_convs:
        if q_low in c["title"].lower():
            return c

    matches = search_conversations(q, limit=1)
    if matches:
        target_id = matches[0]["id"]
        for c in all_convs:
            if c["id"] == target_id:
                return c
    return None


def search_conversations(query_str, limit=15):
    """Searches conversations across both Desktop and CLI by title, user prompts, and agent responses."""
    q = query_str.strip()
    if not q:
        return list_all_conversations(include_archived=True)[:limit]

    all_convs = {c["id"]: c for c in list_all_conversations(include_archived=True)}
    matched_ids = []
    snippets = {}

    rpc_res = ls_rpc("SearchConversations", {"query": q})
    if rpc_res and isinstance(rpc_res.get("results"), list):
        for r in rpc_res["results"]:
            cid = r.get("cascadeId")
            if cid in all_convs and cid not in matched_ids:
                matched_ids.append(cid)
                snip = (r.get("snippet") or "").replace("\n", " ").strip()
                if snip:
                    snippets[cid] = snip[:140]

    q_low = q.lower()
    for cid, c in all_convs.items():
        if q_low in c["title"].lower() or q_low in cid.lower():
            if cid not in matched_ids:
                matched_ids.append(cid)

    for db_file in (DB_PATH, CLI_DB_PATH):
        if len(matched_ids) >= limit or not os.path.exists(db_file):
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            like_q = f"%{q}%"
            rows = cur.execute("""
                SELECT c0, c10, c13, c14
                FROM conversations_fts_content
                WHERE c10 LIKE ? OR c13 LIKE ? OR c14 LIKE ?
                LIMIT 50
            """, (like_q, like_q, like_q)).fetchall()
            conn.close()
            for cid, t_txt, u_txt, a_txt in rows:
                if cid in all_convs and cid not in matched_ids:
                    matched_ids.append(cid)
                    for body in (u_txt, a_txt):
                        if body and isinstance(body, str) and q_low in body.lower():
                            pos = body.lower().find(q_low)
                            start = max(0, pos - 40)
                            snippets[cid] = body[start:start + 120].replace("\n", " ").strip()
                            break
        except Exception:
            pass

    results = []
    for cid in matched_ids[:limit]:
        item = dict(all_convs[cid])
        if cid in snippets:
            item["snippet"] = snippets[cid]
        results.append(item)
    return results


def extract_conversation_turns(cid):
    """
    Extracts all (role, step_idx, text, timestamp) turns for any conversation ID `cid`.
    Works across:
    1) brain/<cid>/.system_generated/logs/transcript_full.jsonl (Desktop & CLI)
    2) LanguageServer ConvertTrajectoryToMarkdown RPC
    3) conversation_summaries.db FTS content chunks (Desktop & CLI)
    """
    candidates = [
        os.path.join(BRAIN_DIR, cid, ".system_generated", "logs", "transcript_full.jsonl"),
        os.path.join(BRAIN_DIR, cid, ".system_generated", "logs", "transcript.jsonl"),
        os.path.join(CLI_BRAIN_DIR, cid, ".system_generated", "logs", "transcript_full.jsonl"),
        os.path.join(CLI_BRAIN_DIR, cid, ".system_generated", "logs", "transcript.jsonl"),
    ]
    path = next((p for p in candidates if os.path.exists(p)), None)
    turns = []

    if path:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                        stype = s.get("type")
                        content = (s.get("content") or "").strip()
                        if not content:
                            continue
                        s_idx = s.get("step_index", 0)
                        ts = (s.get("created_at") or "")[:16].replace("T", " ")
                        if stype == "USER_INPUT":
                            m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, re.DOTALL)
                            u_txt = m.group(1).strip() if m else content
                            if u_txt:
                                turns.append(("USER", s_idx, u_txt, ts))
                        elif stype == "PLANNER_RESPONSE" and s.get("source") == "MODEL":
                            turns.append(("AGENT", s_idx, content, ts))
                    except Exception:
                        continue
        except Exception:
            pass

    if turns:
        return turns

    rpc_md = ls_rpc("ConvertTrajectoryToMarkdown", {"conversationId": cid})
    if rpc_md and rpc_md.get("markdown"):
        md = rpc_md["markdown"]
        parts = re.split(r"^### (User Input|Planner Response)\s*$", md, flags=re.MULTILINE)
        idx = 1
        step_counter = 1
        while idx + 1 < len(parts):
            role_label = parts[idx].strip()
            body = parts[idx + 1].strip()
            if body:
                role = "USER" if role_label == "User Input" else "AGENT"
                if role == "USER":
                    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", body, re.DOTALL)
                    body = m.group(1).strip() if m else body
                turns.append((role, step_counter, body, ""))
                step_counter += 1
            idx += 2
        if turns:
            return turns

    for db_file in (DB_PATH, CLI_DB_PATH):
        if not os.path.exists(db_file):
            continue
        try:
            conn = sqlite3.connect(db_file)
            cur = conn.cursor()
            chunks = cur.execute(
                "SELECT c1, c13, c14 FROM conversations_fts_content WHERE c0=? AND c1 >= 0 ORDER BY c1",
                (cid,)
            ).fetchall()
            conn.close()
            for c_idx, u_body, a_body in chunks:
                if u_body and isinstance(u_body, str) and u_body.strip():
                    turns.append(("USER", c_idx * 10, u_body.strip(), ""))
                if a_body and isinstance(a_body, str) and a_body.strip():
                    turns.append(("AGENT", c_idx * 10 + 1, a_body.strip(), ""))
            if turns:
                return turns
        except Exception:
            pass

    return turns


# ─────────────────────────────────────────────────────────────────────────────
# 3. Language-Adaptive Reports (English & Arabic)
# ─────────────────────────────────────────────────────────────────────────────

def format_conversations_list_report(page=1, per_page=20, only_archived=False, include_archived=True, source_filter=None, lang="en"):
    convs = list_all_conversations(include_archived=include_archived, only_archived=only_archived, source_filter=source_filter)
    total = len(convs)
    if total == 0:
        return "📭 **لا توجد محادثات مطابقة.**" if lang == "ar" else "📭 **No matching conversations found.**"

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    subset = convs[start:start + per_page]

    if lang == "ar":
        if only_archived:
            header_title = "🗄️ **المحادثات المؤرشفة في Antigravity**"
        elif source_filter == "cli":
            header_title = "💻 **سجل محادثات الطرفية (Antigravity CLI)**"
        else:
            header_title = "📚 **سجل محادثات Antigravity (سطح المكتب + CLI)**"
        lines = [
            f"{header_title} — صفحة `{page}/{total_pages}` (الإجمالي: `{total}` محادثة)",
            "────────────────────────────",
        ]
        for c in subset:
            arch_badge = "🗄️ `[مؤرشفة]` " if c["archived"] else ""
            src_badge = "`[CLI]` " if "CLI" in c.get("source", "") else ""
            thread_badge = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            run_badge = " 🟢" if "RUNNING" in (c.get("status") or "") else ""
            lines.append(
                f"**{c['index']}.** `{c['short_id']}` — {src_badge}{arch_badge}**{c['title']}**{run_badge} "
                f"(`{c['steps']}` خطوة، `{c['modified']}`){thread_badge}"
            )
        lines.append("────────────────────────────")
        lines.append(
            "💡 **أوامر سريعة:**\n"
            "- لقراءة أي محادثة أو فتحها في ثريد: `!read <الرقم أو المعرف>` (أو `!read <الرقم> all`)\n"
            "- للبحث في المحادثات: `!search <كلمة>` | للصفحات التالية: `!chats 2`\n"
            "- للأرشفة أو إلغاء الأرشفة: `!archive <الرقم>` أو `!unarchive <الرقم>`\n"
            "- لتشغيل أمر عبر CLI: `!cli <سؤالك>` | لعرض الجلسات النشطة: `!sessions`"
        )
    else:
        if only_archived:
            header_title = "🗄️ **Archived Antigravity Conversations**"
        elif source_filter == "cli":
            header_title = "💻 **Antigravity CLI Conversations**"
        else:
            header_title = "📚 **Antigravity Conversations (Desktop + CLI)**"
        lines = [
            f"{header_title} — Page `{page}/{total_pages}` (Total: `{total}`)",
            "────────────────────────────",
        ]
        for c in subset:
            arch_badge = "🗄️ `[Archived]` " if c["archived"] else ""
            src_badge = "`[CLI]` " if "CLI" in c.get("source", "") else ""
            thread_badge = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            run_badge = " 🟢" if "RUNNING" in (c.get("status") or "") else ""
            lines.append(
                f"**{c['index']}.** `{c['short_id']}` — {src_badge}{arch_badge}**{c['title']}**{run_badge} "
                f"(`{c['steps']}` steps, `{c['modified']}`){thread_badge}"
            )
        lines.append("────────────────────────────")
        lines.append(
            "💡 **Quick Commands:**\n"
            "- Read or open a conversation thread: `!read <num|id>` (or `!read <num|id> all`)\n"
            "- Search all conversations: `!search <query>` | Next page: `!chats 2` | CLI only: `!cli-chats`\n"
            "- Archive / Unarchive: `!archive <num|id>` or `!unarchive <num|id>`\n"
            "- Continue in CLI: `!cli <prompt>` | Fork: `!fork <num|id>` | Stop: `!stop <num|id>`"
        )
    return "\n".join(lines)


def format_search_report(query_str, lang="en"):
    matches = search_conversations(query_str, limit=15)
    if not matches:
        if lang == "ar":
            return f"🔍 **لم يتم العثور على نتائج للبحث عن:** `{query_str}`"
        return f"🔍 **No conversations matched your query:** `{query_str}`"

    if lang == "ar":
        lines = [
            f"🔍 **نتائج البحث في المحادثات عن:** `{query_str}` (عدد النتائج: `{len(matches)}`)",
            "────────────────────────────",
        ]
        for c in matches:
            arch_badge = "🗄️ `[مؤرشفة]` " if c["archived"] else ""
            src_badge = "`[CLI]` " if "CLI" in c.get("source", "") else ""
            thread_badge = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            lines.append(
                f"**{c['index']}.** `{c['short_id']}` — {src_badge}{arch_badge}**{c['title']}** "
                f"(`{c['steps']}` خطوة، `{c['modified']}`){thread_badge}"
            )
            if c.get("snippet"):
                lines.append(f"   > _{c['snippet']}_")
        lines.append("────────────────────────────")
        lines.append("💡 استخدم `!read <الرقم أو المعرف>` لفتح وقراءة أي محادثة من النتائج أعلاه.")
    else:
        lines = [
            f"🔍 **Search Results for:** `{query_str}` (`{len(matches)}` matches)",
            "────────────────────────────",
        ]
        for c in matches:
            arch_badge = "🗄️ `[Archived]` " if c["archived"] else ""
            src_badge = "`[CLI]` " if "CLI" in c.get("source", "") else ""
            thread_badge = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            lines.append(
                f"**{c['index']}.** `{c['short_id']}` — {src_badge}{arch_badge}**{c['title']}** "
                f"(`{c['steps']}` steps, `{c['modified']}`){thread_badge}"
            )
            if c.get("snippet"):
                lines.append(f"   > _{c['snippet']}_")
        lines.append("────────────────────────────")
        lines.append("💡 Use `!read <num|id>` (or `!read <num|id> all`) to open and read any conversation above.")
    return "\n".join(lines)


def read_and_post_conversation_to_discord(token, general_channel_id, source_channel_id, author_id, query_arg, lang="en"):
    """
    Handles `!read <id_or_index> [all|<N>]`:
    1. Resolves the target conversation (or uses the current thread's conversation if `!read all` is called inside a thread!).
    2. Ensures a dedicated Discord thread exists for it (creates one in #general if not).
    3. Formats and sends the conversation turns into that thread using language-adaptive layout.
    """
    import discord_hook

    parts = query_arg.strip().split()
    threads = load_json(THREADS_FILE, {})

    # Allow `!read` or `!read all` inside an existing conversation thread without typing the ID!
    target_q = None
    mode = "recent"
    if not parts or parts[0].lower() in ("all", "full", "export", "الكل", "كامل", "كاملة"):
        if source_channel_id != general_channel_id:
            for cid_k, info in threads.items():
                if isinstance(info, dict) and info.get("thread_id") == source_channel_id and len(cid_k) == 36:
                    target_q = cid_k
                    mode = parts[0].lower() if parts else "all"
                    break
        if not target_q:
            if lang == "ar":
                return "⚠️ **يرجى تحديد رقم المحادثة أو معرفها.** مثال: `!read 3` أو `!read 068a0a41` أو `!chats` لعرض القائمة."
            return "⚠️ **Please specify a conversation number or ID.** Example: `!read 3`, `!read 068a0a41`, or run `!chats` to list all sessions."
    else:
        target_q = parts[0]
        mode = parts[1].lower() if len(parts) > 1 else "recent"

    conv = resolve_conversation_query(target_q)
    if not conv:
        if lang == "ar":
            return f"⚠️ **لم يتم العثور على محادثة تطابق:** `{target_q}`\nاستخدم `!chats` أو `!search <كلمة>` للبحث."
        return f"⚠️ **No conversation found matching:** `{target_q}`\nUse `!chats` or `!search <query>` to find it."

    cid = conv["id"]
    title = conv["title"]
    is_arch = conv["archived"]
    src_label = conv.get("source", "Desktop")
    turns = extract_conversation_turns(cid)
    if not turns:
        if lang == "ar":
            return f"⚠️ **المحادثة `{cid[:8]}` ({title}) لا تحتوي على رسائل نصية قابلة للعرض.**"
        return f"⚠️ **Conversation `{cid[:8]}` ({title}) has no readable text turns.**"

    conv_lang = "ar" if (lang == "ar" or discord_hook.is_arabic_text(title)) else "en"

    existing_thread_id = None
    if cid in threads and isinstance(threads[cid], dict):
        existing_thread_id = threads[cid].get("thread_id")

    if existing_thread_id:
        try:
            ch_obj = discord_api(token, "GET", f"/channels/{existing_thread_id}")
            if ch_obj and ch_obj.get("thread_metadata", {}).get("archived"):
                discord_api(token, "PATCH", f"/channels/{existing_thread_id}", {"archived": False})
        except Exception:
            existing_thread_id = None

    if not existing_thread_id:
        prefix = "🗄️" if is_arch else "💬"
        thread_name = f"{prefix} {title}"[:95]
        if conv_lang == "ar":
            starter_content = (
                f"📖 **سجل محادثة مسترجع ({'مؤرشفة' if is_arch else 'نشطة'} — `{src_label}`):** "
                f"**{title}** (`{cid[:8]}` — `{conv['steps']}` خطوة)"
            )
        else:
            starter_content = (
                f"📖 **Restored Session ({'Archived' if is_arch else 'Active'} — `{src_label}`):** "
                f"**{title}** (`{cid[:8]}` — `{conv['steps']}` steps)"
            )
        try:
            starter = discord_api(
                token,
                "POST",
                f"/channels/{general_channel_id}/messages",
                {"content": starter_content},
            )
            starter_id = starter.get("id")
            if starter_id:
                t_obj = discord_api(
                    token,
                    "POST",
                    f"/channels/{general_channel_id}/messages/{starter_id}/threads",
                    {"name": thread_name, "auto_archive_duration": 10080},
                )
                existing_thread_id = t_obj.get("id")
                if existing_thread_id:
                    if author_id:
                        try:
                            discord_api(token, "PUT", f"/channels/{existing_thread_id}/thread-members/{author_id}")
                        except Exception:
                            pass
                    threads[cid] = {
                        "thread_id": existing_thread_id,
                        "title": thread_name,
                        "starter_id": starter_id,
                        "last_author_id": author_id,
                    }
                    save_json(THREADS_FILE, threads)
        except Exception:
            existing_thread_id = source_channel_id

    target_tid = existing_thread_id or source_channel_id

    total_turns = len(turns)
    if mode in ("all", "full", "الكل", "كامل", "كاملة"):
        selected_turns = turns
        mode_desc = f"كامل المحادثة (`{total_turns}` رسالة)" if lang == "ar" else f"Full transcript (`{total_turns}` messages)"
    elif mode.isdigit():
        n = max(1, min(int(mode), total_turns))
        selected_turns = turns[-n:]
        mode_desc = f"آخر `{n}` من أصل `{total_turns}` رسالة" if lang == "ar" else f"Last `{n}` of `{total_turns}` messages"
    else:
        if total_turns <= 12:
            selected_turns = turns
            mode_desc = f"كامل المحادثة (`{total_turns}` رسالة)" if lang == "ar" else f"Full transcript (`{total_turns}` messages)"
        else:
            skipped = total_turns - 10
            if lang == "ar":
                div_txt = f"... (تم تخطي {skipped} رسالة وسطى — أرسل `!read {cid[:8]} all` لعرض الكل) ..."
                mode_desc = f"ملخص السجل (أول رسالتين وآخر 8 رسائل من أصل `{total_turns}` رسالة)"
            else:
                div_txt = f"... ({skipped} middle messages omitted — send `!read {cid[:8]} all` for full history or check the attached Markdown file) ..."
                mode_desc = f"Overview (first 2 + last 8 of `{total_turns}` messages)"
            selected_turns = turns[:2] + [("DIVIDER", 0, div_txt, "")] + turns[-8:]

    if lang == "ar":
        header = (
            f"📖 **استعراض المحادثة:** **{title}**\n"
            f"- **المعرف**: `{cid}` (`{src_label}`)\n"
            f"- **الحالة**: `{'مؤرشفة 🗄️' if is_arch else 'نشطة 💬'}` | **آخر تعديل**: `{conv['modified']}` | **العرض**: {mode_desc}\n"
            f"────────────────────────────"
        )
    else:
        header = (
            f"📖 **Conversation Transcript:** **{title}**\n"
            f"- **ID**: `{cid}` (`{src_label}`)\n"
            f"- **Status**: `{'Archived 🗄️' if is_arch else 'Active 💬'}` | **Modified**: `{conv['modified']}` | **View**: {mode_desc}\n"
            f"────────────────────────────"
        )
    discord_hook.send_discord_rich(token, target_tid, header, is_user_prompt=False, ping_user=False)

    full_md_lines = [
        f"# {title}",
        f"- **Conversation ID**: `{cid}`",
        f"- **Source**: {src_label}",
        f"- **Status**: {'Archived' if is_arch else 'Active'}",
        f"- **Last Modified**: {conv['modified']}",
        f"- **Total Messages**: {total_turns}",
        "",
        "---",
        "",
    ]
    for r_role, r_idx, r_txt, r_ts in turns:
        role_hdr = "👤 User" if r_role == "USER" else "🤖 Antigravity"
        ts_str = f" ({r_ts})" if r_ts else ""
        full_md_lines.append(f"## {role_hdr}{ts_str} [Step {r_idx}]\n\n{r_txt}\n\n---\n")

    scratch_dir = "/home/k/.gemini/antigravity/scratch/discord_exports"
    os.makedirs(scratch_dir, exist_ok=True)
    export_path = os.path.join(scratch_dir, f"conversation_{cid[:8]}.md")
    try:
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("\n".join(full_md_lines))
    except Exception:
        export_path = None

    max_msgs_to_post = 25
    posted = 0
    for role, s_idx, txt, ts in selected_turns:
        if posted >= max_msgs_to_post:
            trunc_msg = (
                f"⚠️ **تم إيقاف العرض المباشر عند {max_msgs_to_post} رسالة لمنع ازدحام القناة. السجل الكامل مرفق أدناه في ملف Markdown.**"
                if lang == "ar"
                else f"⚠️ **Inline output capped at {max_msgs_to_post} messages to avoid flooding. The complete transcript is attached below as Markdown.**"
            )
            discord_hook.send_discord_rich(token, target_tid, trunc_msg, is_user_prompt=False, ping_user=False)
            break
        if role == "DIVIDER":
            discord_hook.send_discord_rich(token, target_tid, f"*{txt}*", is_user_prompt=False, ping_user=False)
        elif role == "USER":
            discord_hook.send_discord_rich(token, target_tid, txt, is_user_prompt=True, ping_user=False)
        else:
            discord_hook.send_discord_rich(token, target_tid, txt, is_user_prompt=False, ping_user=False)
        posted += 1
        time.sleep(0.35)

    if export_path and os.path.exists(export_path) and (total_turns > 12 or mode in ("all", "full", "export", "الكل", "كامل", "كاملة")):
        discord_hook.upload_file_to_discord(token, target_tid, export_path)

    if turns:
        max_step = max(t[1] for t in turns if isinstance(t[1], int))
        st = load_json(STATE_FILE, {})
        if max_step > st.get(cid, -1):
            st[cid] = max_step
            save_json(STATE_FILE, st)

    if source_channel_id != target_tid:
        if lang == "ar":
            return (
                f"✅ **تم استرجاع وعرض المحادثة `{cid[:8]}` ({title}) في الثريد المخصص:** <#{target_tid}>\n"
                f"يمكنك الدخول إلى <#{target_tid}> لقراءتها أو إرسال رسائل جديدة فيها لمواصلة المحادثة مباشرة (عبر سطح المكتب أو `!cli`)!"
            )
        return (
            f"✅ **Loaded conversation `{cid[:8]}` ({title}) into thread:** <#{target_tid}>\n"
            f"Jump into <#{target_tid}> to read it or send new messages (or `!cli <prompt>`) to continue the session!"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Bi-Directional Archive Synchronization (Antigravity <-> Discord)
# ─────────────────────────────────────────────────────────────────────────────

MAX_ACTIVE_DISCORD_THREADS = 15


def prune_discord_threads_to_limit(token, max_active=MAX_ACTIVE_DISCORD_THREADS):
    """
    Enforces that at most `max_active` threads (the most recent conversations) remain active on Discord.
    Any older active threads beyond the top `max_active` are archived on Discord so they don't clutter
    the sidebar or cause heavy polling loads, but remain 100% searchable via Discord search, !search,
    !chats, and !read.
    """
    try:
        threads = load_json(THREADS_FILE, {})
        arch_state = load_json(ARCHIVE_STATE_FILE, {})
        convs = list_all_conversations(include_archived=True)
        cids_with_threads = [
            c["id"] for c in convs
            if c["id"] in threads and isinstance(threads.get(c["id"]), dict) and threads[c["id"]].get("thread_id")
        ]
        keep_ids = set(cids_with_threads[:max_active])
        changed = False

        # Ensure top `max_active` threads are unarchived on Discord and active locally
        for cid in list(keep_ids):
            tinfo = threads.get(cid)
            if not tinfo or not tinfo.get("thread_id"):
                continue
            tid = tinfo["thread_id"]
            if tinfo.get("archived") is not False:
                try:
                    discord_api(token, "PATCH", f"/channels/{tid}", {"archived": False, "auto_archive_duration": 10080})
                    tinfo["archived"] = False
                    arch_state[cid] = False
                    set_conv_archived_locally(cid, False)
                    changed = True
                    time.sleep(0.3)
                except Exception:
                    pass

        # Archive any active thread beyond `max_active`
        for cid in cids_with_threads[max_active:]:
            tinfo = threads.get(cid)
            if not tinfo or not tinfo.get("thread_id"):
                continue
            tid = tinfo["thread_id"]
            if tinfo.get("archived") is not True:
                try:
                    discord_api(token, "PATCH", f"/channels/{tid}", {"archived": True})
                    tinfo["archived"] = True
                    arch_state[cid] = True
                    set_conv_archived_locally(cid, True)
                    changed = True
                    time.sleep(0.2)
                except Exception:
                    pass

        if changed:
            save_json(THREADS_FILE, threads)
            save_json(ARCHIVE_STATE_FILE, arch_state)
    except Exception:
        pass


def _is_discord_auto_archive(ch_info):
    """Heuristic: archived roughly `auto_archive_duration` minutes after the last message => Discord inactivity auto-archive."""
    try:
        from datetime import datetime
        md = ch_info.get("thread_metadata") or {}
        arch_ts = datetime.fromisoformat(md["archive_timestamp"].replace("Z", "+00:00")).timestamp()
        last_id = int(ch_info.get("last_message_id") or ch_info["id"])
        last_ts = ((last_id >> 22) + 1420070400000) / 1000.0
        dur = int(md.get("auto_archive_duration") or 10080) * 60
        return (arch_ts - last_ts) >= dur - 900
    except Exception:
        return False


def sync_archives(token, general_channel_id, guild_id=None):
    """
    Two-way synchronization between Antigravity (`annotations/*.pbtxt`) and Discord threads:
    1. For all conversations that have a thread in `discord_threads.json`:
       - If archived on Desktop -> archives the Discord thread (and prefixes title with 🗄️).
       - If unarchived on Desktop -> unarchives the Discord thread.
       - If archived by user on Discord -> marks `archived:true` on Desktop via `UpdateConversationAnnotations`.
       - If unarchived by user on Discord -> marks `archived:false` on Desktop.
    2. For any conversation archived on Desktop that does NOT yet have a Discord thread:
       - Creates its archived summary thread on Discord using language matching the conversation title.
    """
    import fcntl
    import discord_hook

    lock_file = os.path.join(CONFIG_DIR, "discord_archive.lock")
    lock_fd = None
    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o666)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except Exception:
                pass
        return

    try:
        threads = load_json(THREADS_FILE, {})
        arch_state = load_json(ARCHIVE_STATE_FILE, {})
        threads_changed = False
        arch_changed = False

        # Only sync the top MAX_ACTIVE_DISCORD_THREADS (15) active threads to prevent rate limiting or loop latency
        convs = list_all_conversations(include_archived=True)
        cids_with_threads = [
            c["id"] for c in convs
            if c["id"] in threads and isinstance(threads.get(c["id"]), dict) and threads[c["id"]].get("thread_id")
        ]
        active_sync_cids = set(cids_with_threads[:MAX_ACTIVE_DISCORD_THREADS])

        for cid in active_sync_cids:
            info = threads.get(cid)
            if not isinstance(info, dict) or not info.get("thread_id"):
                continue
            tid = info["thread_id"]
            local_arch = is_conv_archived_locally(cid)
            prev_known = arch_state.get(cid)

            remote_arch = None
            remote_name = info.get("title", "")
            try:
                ch_info = discord_api(token, "GET", f"/channels/{tid}")
                if ch_info and "thread_metadata" in ch_info:
                    remote_arch = bool(ch_info["thread_metadata"].get("archived", False))
                    remote_name = ch_info.get("name") or remote_name
            except Exception:
                continue

            if remote_arch is None:
                continue

            if prev_known is None:
                if local_arch != remote_arch:
                    if local_arch:
                        try:
                            new_name = re.sub(r"^[💬🗄️]\s*", "🗄️ ", remote_name)[:95]
                            discord_api(token, "PATCH", f"/channels/{tid}", {"name": new_name, "archived": True})
                            info["title"] = new_name
                            info["archived"] = True
                            threads_changed = True
                        except Exception:
                            pass
                    else:
                        try:
                            discord_api(token, "PATCH", f"/channels/{tid}", {"archived": False})
                        except Exception:
                            pass
                arch_state[cid] = local_arch
                arch_changed = True
            else:
                if local_arch != prev_known and remote_arch == prev_known:
                    try:
                        if local_arch:
                            new_name = re.sub(r"^[💬🗄️]\s*", "🗄️ ", remote_name)[:95]
                            discord_api(token, "PATCH", f"/channels/{tid}", {"name": new_name, "archived": True})
                            info["title"] = new_name
                            info["archived"] = True
                        else:
                            new_name = re.sub(r"^[💬🗄️]\s*", "💬 ", remote_name)[:95]
                            discord_api(token, "PATCH", f"/channels/{tid}", {"archived": False})
                            discord_api(token, "PATCH", f"/channels/{tid}", {"name": new_name})
                            info["title"] = new_name
                            info["archived"] = False
                        threads_changed = True
                        arch_state[cid] = local_arch
                        arch_changed = True
                    except Exception:
                        pass
                elif remote_arch != prev_known and local_arch == prev_known:
                    convs = list_all_conversations(include_archived=True)
                    cids_with_threads = [
                        c["id"] for c in convs
                        if c["id"] in threads and isinstance(threads.get(c["id"]), dict) and threads[c["id"]].get("thread_id")
                    ]
                    # If this is among the top MAX_ACTIVE_DISCORD_THREADS, ensure it stays unarchived
                    if cid in cids_with_threads[:MAX_ACTIVE_DISCORD_THREADS]:
                        if remote_arch:
                            try:
                                discord_api(token, "PATCH", f"/channels/{tid}", {"archived": False, "auto_archive_duration": 10080})
                                info["archived"] = False
                                threads_changed = True
                                arch_state[cid] = False
                                arch_changed = True
                            except Exception:
                                pass
                        continue

                    if remote_arch and _is_discord_auto_archive(ch_info):
                        continue
                    set_conv_archived_locally(cid, bool(remote_arch))
                    info["archived"] = bool(remote_arch)
                    threads_changed = True
                    arch_state[cid] = bool(remote_arch)
                    arch_changed = True
                elif local_arch != prev_known and remote_arch != prev_known:
                    arch_state[cid] = local_arch
                    arch_changed = True

        if threads_changed:
            save_json(THREADS_FILE, threads)
        if arch_changed:
            save_json(ARCHIVE_STATE_FILE, arch_state)

        # Enforce that only the top 15 most recent threads remain active in Discord
        prune_discord_threads_to_limit(token, MAX_ACTIVE_DISCORD_THREADS)
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except Exception:
                pass


def toggle_archive_command(token, general_channel_id, source_channel_id, query_arg, archive=True, lang="en"):
    """Handles `!archive [id_or_index]` and `!unarchive [id_or_index]`."""
    threads = load_json(THREADS_FILE, {})
    arch_state = load_json(ARCHIVE_STATE_FILE, {})

    target_conv = None
    q = query_arg.strip()
    if q:
        target_conv = resolve_conversation_query(q)
    else:
        if source_channel_id != general_channel_id:
            for cid, info in threads.items():
                if isinstance(info, dict) and info.get("thread_id") == source_channel_id and len(cid) >= 32:
                    target_conv = resolve_conversation_query(cid)
                    break

    if not target_conv:
        if lang == "ar":
            return "⚠️ **يرجى تحديد رقم المحادثة أو معرفها، أو إرسال الأمر داخل ثريد المحادثة.** مثال: `!archive 5`"
        return "⚠️ **Please specify a conversation number or ID, or run this command inside a conversation thread.** Example: `!archive 5`"

    cid = target_conv["id"]
    title = target_conv["title"]
    set_conv_archived_locally(cid, archived=archive)
    arch_state[cid] = bool(archive)
    save_json(ARCHIVE_STATE_FILE, arch_state)

    tid = target_conv.get("thread_id")
    if tid:
        try:
            if archive:
                new_name = f"🗄️ {title}"[:95]
                discord_api(token, "PATCH", f"/channels/{tid}", {"name": new_name, "archived": True})
            else:
                new_name = f"💬 {title}"[:95]
                discord_api(token, "PATCH", f"/channels/{tid}", {"archived": False})
                discord_api(token, "PATCH", f"/channels/{tid}", {"name": new_name})
            if cid in threads and isinstance(threads[cid], dict):
                threads[cid]["archived"] = bool(archive)
                threads[cid]["title"] = new_name
                save_json(THREADS_FILE, threads)
        except Exception:
            pass
    elif archive:
        sync_archives(token, general_channel_id)

    icon = "🗄️" if archive else "📂"
    if lang == "ar":
        action_ar = "أرشفة" if archive else "إلغاء أرشفة"
        return f"{icon} **تمت {action_ar} المحادثة بنجاح في Antigravity وديسكورد:** `{cid[:8]}` — **{title}**"
    action_en = "Archived" if archive else "Unarchived"
    return f"{icon} **{action_en} conversation on both Antigravity & Discord:** `{cid[:8]}` — **{title}**"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Max-Power Session Control (Desktop & CLI)
# ─────────────────────────────────────────────────────────────────────────────

def get_sessions_report(lang="en"):
    """Returns a live overview of Desktop, Language Server, CLI, active conversations, and background tasks."""
    desk_running = is_antigravity_running()
    ls_addr, csrf, ls_pid = discover_language_server()

    cli_ver = "unknown"
    try:
        cli_ver = subprocess.check_output([AGY_CLI_BIN, "--version"], stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        pass

    active_cli_procs = []
    try:
        ps_out = subprocess.check_output(["ps", "-eo", "pid,etimes,args"], stderr=subprocess.DEVNULL).decode()
        for line in ps_out.splitlines():
            if "/bin/agy" in line or "antigravity-cli" in line:
                if "pgrep" not in line and "--version" not in line:
                    active_cli_procs.append(line.strip())
    except Exception:
        pass

    all_convs = list_all_conversations(include_archived=False)
    running_convs = [c for c in all_convs if "RUNNING" in (c.get("status") or "")]
    recent_convs = all_convs[:8]

    if lang == "ar":
        lines = [
            "⚡ **لوحة التحكم بالجلسات (Antigravity Desktop & CLI)**",
            "────────────────────────────",
            f"- **تطبيق سطح المكتب (Desktop)**: {'🟢 يعمل' if desk_running else '🔴 مغلق (`!open` لفتحه)'}",
            f"- **خادم اللغة (Language Server)**: {f'🟢 متصل (`{ls_addr}`، PID `{ls_pid}`)' if ls_addr else '🔴 غير متصل'}",
            f"- **واجهة الطرفية (CLI)**: `{cli_ver}` (`{AGY_CLI_BIN}`)",
            f"- **عمليات CLI قيد التنفيذ حالياً**: `{len(active_cli_procs)}`",
        ]
        if running_convs:
            lines.append("\n🟢 **المحادثات الجارية الآن (RUNNING):**")
            for c in running_convs:
                t_link = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
                lines.append(f"- **#{c['index']}** `{c['short_id']}` (`{c['source']}`) — **{c['title']}** (`{c['steps']}` خطوة){t_link}")
        lines.append("\n🕒 **أحدث الجلسات النشطة:**")
        for c in recent_convs:
            st_icon = "🟢" if "RUNNING" in (c.get("status") or "") else "⚪"
            t_link = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            lines.append(f"- {st_icon} **#{c['index']}** `{c['short_id']}` `[`{c['source']}`]` — **{c['title']}** (`{c['steps']}` خطوة، `{c['modified']}`){t_link}")
        lines.append(
            "────────────────────────────\n"
            "💡 **أوامر التحكم الفوري:**\n"
            "- `!cli <سؤالك>` — تشغيل أو استئناف الجلسة عبر الطرفية (`agy`)\n"
            "- `!stop [الرقم أو المعرف]` — إيقاف جلسة عالقة أو قيد التشغيل\n"
            "- `!fork [الرقم أو المعرف]` — نسخ/تفريع جلسة إلى ثريد جديد\n"
            "- `!models` — عرض النماذج المتاحة والحصة المتبقية"
        )
    else:
        lines = [
            "⚡ **Antigravity Session Control Center (Desktop & CLI)**",
            "────────────────────────────",
            f"- **Desktop App**: {'🟢 Running' if desk_running else '🔴 Closed (run `!open` to launch)'}",
            f"- **Language Server**: {f'🟢 Connected (`{ls_addr}`, PID `{ls_pid}`)' if ls_addr else '🔴 Offline'}",
            f"- **CLI Binary (`agy`)**: `{cli_ver}` (`{AGY_CLI_BIN}`)",
            f"- **Active Headless CLI Runs**: `{len(active_cli_procs)}`",
        ]
        if running_convs:
            lines.append("\n🟢 **Currently Running Cascades:**")
            for c in running_convs:
                t_link = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
                lines.append(f"- **#{c['index']}** `{c['short_id']}` (`{c['source']}`) — **{c['title']}** (`{c['steps']}` steps){t_link}")
        lines.append("\n🕒 **Most Recent Active Sessions:**")
        for c in recent_convs:
            st_icon = "🟢" if "RUNNING" in (c.get("status") or "") else "⚪"
            t_link = f" 🔗 <#{c['thread_id']}>" if c.get("thread_id") else ""
            lines.append(f"- {st_icon} **#{c['index']}** `{c['short_id']}` `[`{c['source']}`]` — **{c['title']}** (`{c['steps']}` steps, `{c['modified']}`){t_link}")
        lines.append(
            "────────────────────────────\n"
            "💡 **Power Controls:**\n"
            "- `!cli <prompt>` — Run or continue session in headless CLI (`agy`)\n"
            "- `!stop [num|id]` — Cancel/stop a running or stuck session\n"
            "- `!fork [num|id]` — Fork a conversation into a new branch thread\n"
            "- `!models` — Inspect available models & remaining quotas\n"
            "- `!sh <command>` — Execute a host shell command"
        )
    return "\n".join(lines)


MODEL_ALIASES = {
    # short alias -> agy CLI model id
    "argon": "gemini-4-argon-high", "pro": "gemini-4-argon-high", "gemini": "gemini-4-argon-high",
    "flash": "gemini-3.8-flash-medium", "fast": "gemini-3.8-flash-low",
    "opus": "claude-opus-5-5-high", "claude": "claude-opus-5-5-high",
    "sonnet": "claude-sonnet-5-5-medium", "gpt": "gpt-oss-120b-medium", "oss": "gpt-oss-120b-medium",
}


def resolve_model_alias(name, effort=None):
    """Maps friendly names (`opus`, `flash`, `argon-low`, `Claude Opus 5.5 (High)`) to real agy model ids."""
    if not name:
        return None
    n = name.strip().lower()
    m = re.match(r"^(.*?)\s*\((low|medium|high)\)$", n)
    if m:
        n = re.sub(r"[\s.]+", "-", m.group(1)) + "-" + m.group(2)
    base, _, eff = n.rpartition("-")
    if eff in ("low", "medium", "high") and base in MODEL_ALIASES:
        return re.sub(r"-(low|medium|high)$", f"-{eff}", MODEL_ALIASES[base])
    if n in MODEL_ALIASES:
        mid = MODEL_ALIASES[n]
        if effort in ("low", "medium", "high"):
            mid = re.sub(r"-(low|medium|high)$", f"-{effort}", mid)
        return mid
    return n.replace("5.5", "5-5")


def _label_to_id(label):
    m = re.match(r"^(.*?)\s*\((\w+)\)$", label.strip())
    base, eff = (m.group(1), m.group(2)) if m else (label, "")
    mid = re.sub(r"[\s]+", "-", base.lower()).replace("claude-opus-5.5", "claude-opus-5-5").replace("claude-sonnet-5.5", "claude-sonnet-5-5")
    return f"{mid}-{eff.lower()}" if eff else mid


def get_models_report(lang="en"):
    """Lists models grouped by family with live quota (Desktop LS `GetUserStatus`), falling back to `agy models`."""
    res = ls_rpc("GetUserStatus", {})
    us = (res or {}).get("userStatus", {})
    configs = us.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
    ar = lang == "ar"
    source = "Desktop"

    if not configs:
        # Desktop LS offline -> ask the CLI (no quota info available there)
        try:
            out = subprocess.run([AGY_CLI_BIN, "models"], capture_output=True, text=True, timeout=25).stdout
            for line in out.splitlines():
                if "\t" in line:
                    mid, label = line.split("\t", 1)
                    configs.append({"label": label.strip(), "_id": mid.strip()})
            source = "agy CLI"
        except Exception:
            pass
    if not configs:
        return ("⚠️ **تعذر جلب النماذج:** خادم اللغة و `agy` غير متاحين. أرسل `!open`."
                if ar else "⚠️ **Could not fetch models:** Desktop LS and `agy` are both unavailable. Send `!open`.")

    order = {"low": 0, "medium": 1, "high": 2}
    families = {}
    for c in configs:
        label = c.get("label", "Unknown")
        m = re.match(r"^(.*?)\s*\((\w+)\)$", label)
        fam, eff = (m.group(1), m.group(2)) if m else (label, "")
        q = c.get("quotaInfo")
        if q is None:
            rem, q = None, {}
        else:
            # proto3 JSON omits zero values: quotaInfo present without remainingFraction == 0% (exhausted)
            rem = q.get("remainingFraction", 0.0)
        f = families.setdefault(fam, {"efforts": [], "rem": rem, "reset": q.get("resetTime") or "", "ids": []})
        f["efforts"].append(eff)
        f["ids"].append(c.get("modelId") or c.get("_id") or _label_to_id(label))
        if isinstance(rem, (int, float)) and (not isinstance(f["rem"], (int, float)) or rem < f["rem"]):
            f["rem"] = rem

    tier = (us.get("userTier") or {}).get("name") or us.get("planStatus", {}).get("planInfo", {}).get("planName") or "?"
    head = (f"🧠 **النماذج المتاحة** (الباقة: `{tier}` · المصدر: `{source}`)" if ar
            else f"🧠 **Available models** (plan: `{tier}` · source: `{source}`)")
    lines = [head, "────────────────────────────"]

    def sort_key(item):
        r = item[1]["rem"]
        return (-(r if isinstance(r, (int, float)) else 2), item[0])

    for fam, f in sorted(families.items(), key=sort_key):
        effs = "/".join(sorted({e for e in f["efforts"] if e}, key=lambda e: order.get(e.lower(), 9)))
        rem = f["rem"]
        if isinstance(rem, (int, float)):
            pct = f"{rem * 100:.0f}%"
            dot = "🟢" if rem > 0.5 else ("🟡" if rem > 0.15 else "🔴")
        else:
            pct, dot = ("؟" if ar else "n/a"), "⚪"
        reset = f["reset"][:16].replace("T", " ")
        reset_s = (f" · تتجدد `{reset} UTC`" if ar else f" · resets `{reset} UTC`") if reset and isinstance(rem, (int, float)) and rem < 0.999 else ""
        if isinstance(rem, (int, float)) and rem <= 0:
            dot = "⛔"
        lines.append(f"- {dot} **{fam}** `{effs}` — {'المتبقي' if ar else 'left'}: `{pct}`{reset_s}")
    lines.append("────────────────────────────")
    lines.append("ℹ️ " + ("النماذج التي تتشارك نفس الحصة تظهر بنفس النسبة (حصة مشتركة للعائلة)." if ar
                         else "Models sharing one quota pool show the same percentage."))
    lines.append("💡 " + ("مثال: `!cli --model=opus --effort=high <سؤالك>` (أسماء مختصرة: argon, flash, opus, sonnet, gpt)" if ar
                         else "Example: `!cli --model=opus --effort=high <prompt>` (aliases: argon, flash, opus, sonnet, gpt; or full ids like `gemini-4-argon-low`)"))
    return "\n".join(lines)


def stop_conversation_command(general_channel_id, source_channel_id, query_arg, lang="en"):
    """Stops/cancels a running or stuck Desktop cascade (`CancelCascadeInvocation` + `ForceStopCascadeTree`) or CLI run."""
    threads = load_json(THREADS_FILE, {})
    target_conv = None
    q = query_arg.strip()
    if q:
        target_conv = resolve_conversation_query(q)
    elif source_channel_id != general_channel_id:
        for cid, info in threads.items():
            if isinstance(info, dict) and info.get("thread_id") == source_channel_id and len(cid) == 36:
                target_conv = resolve_conversation_query(cid)
                break

    if not target_conv:
        if lang == "ar":
            return "⚠️ **يرجى تحديد رقم المحادثة أو معرفها، أو إرسال `!stop` داخل ثريد المحادثة.**"
        return "⚠️ **Please specify a conversation number or ID, or send `!stop` inside a conversation thread.**"

    cid = target_conv["id"]
    title = target_conv["title"]

    r1 = ls_rpc("CancelCascadeInvocation", {"cascadeId": cid})
    r2 = ls_rpc("ForceStopCascadeTree", {"cascadeId": cid})

    killed_cli = 0
    try:
        out = subprocess.check_output(["pgrep", "-f", f"agy.*{cid[:8]}"], stderr=subprocess.DEVNULL).decode()
        for p in out.split():
            if p.strip().isdigit():
                os.kill(int(p.strip()), 15)
                killed_cli += 1
    except Exception:
        pass

    if lang == "ar":
        return f"🛑 **تم إرسال أمر الإيقاف الفوري للمحادثة:** `{cid[:8]}` — **{title}** (Desktop RPC: `{'تم' if (r1 is not None or r2 is not None) else 'غير متصل'}`، عمليات CLI متوقفة: `{killed_cli}`)."
    return f"🛑 **Sent stop/cancel signal to conversation:** `{cid[:8]}` — **{title}** (Desktop RPC: `{'OK' if (r1 is not None or r2 is not None) else 'N/A'}`, CLI processes terminated: `{killed_cli}`)."


def fork_conversation_command(token, general_channel_id, source_channel_id, author_id, query_arg, lang="en"):
    """Forks an existing conversation via `ForkConversation` RPC (or file clone) and creates a new Discord thread for the fork."""
    threads = load_json(THREADS_FILE, {})
    target_conv = None
    q = query_arg.strip()
    if q:
        target_conv = resolve_conversation_query(q)
    elif source_channel_id != general_channel_id:
        for cid, info in threads.items():
            if isinstance(info, dict) and info.get("thread_id") == source_channel_id and len(cid) == 36:
                target_conv = resolve_conversation_query(cid)
                break

    if not target_conv:
        if lang == "ar":
            return "⚠️ **يرجى تحديد رقم المحادثة أو معرفها لنسخها، أو إرسال `!fork` داخل ثريد المحادثة.**"
        return "⚠️ **Please specify a conversation number or ID to fork, or run `!fork` inside a conversation thread.**"

    cid = target_conv["id"]
    title = target_conv["title"]

    res = ls_rpc("ForkConversation", {"cascadeId": cid})
    new_cid = None
    if isinstance(res, dict):
        new_cid = res.get("newCascadeId") or res.get("cascadeId") or res.get("conversationId")

    if not new_cid:
        import uuid as _uuid
        new_cid = str(_uuid.uuid4())
        for ext in (".pb", ".db"):
            s_p = os.path.join(CONV_DIR, f"{cid}{ext}")
            d_p = os.path.join(CONV_DIR, f"{new_cid}{ext}")
            if os.path.exists(s_p):
                try:
                    shutil.copy2(s_p, d_p)
                except Exception:
                    pass
        s_b = os.path.join(BRAIN_DIR, cid)
        d_b = os.path.join(BRAIN_DIR, new_cid)
        if os.path.exists(s_b) and not os.path.exists(d_b):
            try:
                shutil.copytree(s_b, d_b)
            except Exception:
                pass

    fork_title = f"🔀 Fork: {title}"[:90]
    starter_text = (
        f"🔀 **فرع جديد من المحادثة `{cid[:8]}`:** **{title}** (`{new_cid[:8]}`)"
        if lang == "ar"
        else f"🔀 **Forked from `{cid[:8]}`:** **{title}** (New ID: `{new_cid[:8]}`)"
    )
    try:
        starter = discord_api(token, "POST", f"/channels/{general_channel_id}/messages", {"content": starter_text})
        starter_id = starter.get("id")
        if starter_id:
            t_obj = discord_api(
                token,
                "POST",
                f"/channels/{general_channel_id}/messages/{starter_id}/threads",
                {"name": fork_title, "auto_archive_duration": 10080},
            )
            tid = t_obj.get("id")
            if tid:
                if author_id:
                    try:
                        discord_api(token, "PUT", f"/channels/{tid}/thread-members/{author_id}")
                    except Exception:
                        pass
                threads[new_cid] = {
                    "thread_id": tid,
                    "title": fork_title,
                    "starter_id": starter_id,
                    "last_author_id": author_id,
                }
                save_json(THREADS_FILE, threads)
                if lang == "ar":
                    return f"✅ **تم تفريع المحادثة `{cid[:8]}` إلى جلسة جديدة `{new_cid[:8]}` في الثريد:** <#{tid}>"
                return f"✅ **Forked conversation `{cid[:8]}` into new session `{new_cid[:8]}`:** <#{tid}>"
    except Exception as e:
        if lang == "ar":
            return f"⚠️ **تم إنشاء الفرع `{new_cid[:8]}` ولكن تعذر إنشاء الثريد:** `{e}`"
        return f"⚠️ **Created fork `{new_cid[:8]}`, but failed to create Discord thread:** `{e}`"

    return f"✅ **Forked `{cid[:8]}` -> `{new_cid[:8]}`**"


def run_cli_prompt_command(token, general_channel_id, source_channel_id, author_id, raw_args, lang="en"):
    """
    Executes a prompt via `/home/k/.local/bin/agy` (`!cli [--model=...] [--effort=...] [--conv=<id>] <prompt>`).
    - Runs asynchronously in a background thread/process and posts the response to the target Discord thread!
    - Can resume ANY existing Desktop or CLI conversation when called inside its thread or with `--conv=<id|num>`.
    """
    import discord_hook

    args_text = raw_args.strip()
    if not args_text:
        if lang == "ar":
            return (
                "💻 **استخدام أمر الطرفية (`!cli`):**\n"
                "- داخل أي ثريد محادثة: `!cli <رسالتك>` لاستئناف تلك المحادثة عبر `agy`.\n"
                "- في `#general`: `!cli <رسالتك>` لبدء جلسة CLI جديدة في ثريد مستقل.\n"
                "- خيارات إضافية: `!cli --conv=3 --effort=high <رسالتك>`"
            )
        return (
            "💻 **Antigravity CLI (`!cli`) Usage:**\n"
            "- Inside any conversation thread: `!cli <prompt>` to continue that conversation via headless `agy`.\n"
            "- In `#general`: `!cli <prompt>` to start a new CLI session in a dedicated thread.\n"
            "- Optional flags: `!cli --conv=<num|id> --model=<model> --effort=<low|medium|high|max> <prompt>`"
        )

    model_opt = None
    effort_opt = None
    conv_opt = None
    tokens = args_text.split()
    prompt_tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--model="):
            model_opt = tok.split("=", 1)[1]
        elif tok.startswith("--effort="):
            effort_opt = tok.split("=", 1)[1]
        elif tok.startswith("--conv=") or tok.startswith("--conversation="):
            conv_opt = tok.split("=", 1)[1]
        else:
            prompt_tokens = tokens[i:]
            break
        i += 1

    prompt = " ".join(prompt_tokens).strip()
    if not prompt:
        return "⚠️ **Please provide a prompt after the flags.**" if lang == "en" else "⚠️ **يرجى كتابة السؤال بعد الخيارات.**"

    threads = load_json(THREADS_FILE, {})
    target_cid = None
    target_tid = source_channel_id

    if conv_opt:
        resolved = resolve_conversation_query(conv_opt)
        if resolved:
            target_cid = resolved["id"]
            if resolved.get("thread_id"):
                target_tid = resolved["thread_id"]
    elif source_channel_id != general_channel_id:
        for cid_k, info in threads.items():
            if isinstance(info, dict) and info.get("thread_id") == source_channel_id and len(cid_k) == 36:
                target_cid = cid_k
                break

    # If invoked in #general without an existing thread, create a thread for this CLI session
    if target_tid == general_channel_id:
        clean_title = prompt.replace("\n", " ").strip()[:60]
        t_name = f"💻 {clean_title}"[:95]
        starter_msg = (
            f"💻 **جلسة CLI جديدة:** **{clean_title}**"
            if discord_hook.is_arabic_text(prompt)
            else f"💻 **New CLI Session:** **{clean_title}**"
        )
        try:
            starter = discord_api(token, "POST", f"/channels/{general_channel_id}/messages", {"content": starter_msg})
            s_id = starter.get("id")
            if s_id:
                t_obj = discord_api(
                    token,
                    "POST",
                    f"/channels/{general_channel_id}/messages/{s_id}/threads",
                    {"name": t_name, "auto_archive_duration": 10080},
                )
                if t_obj.get("id"):
                    target_tid = t_obj["id"]
                    if author_id:
                        try:
                            discord_api(token, "PUT", f"/channels/{target_tid}/thread-members/{author_id}")
                        except Exception:
                            pass
        except Exception:
            pass

    if target_cid:
        sync_cli_and_desktop_stores(target_cid=target_cid)

    cmd = [AGY_CLI_BIN]
    if target_cid:
        cmd.extend(["--conversation", target_cid])
    if model_opt:
        cmd.extend(["--model", resolve_model_alias(model_opt, effort_opt)])
    if effort_opt:
        cmd.extend(["--effort", effort_opt])
    cmd.extend(["--dangerously-skip-permissions", "-p", prompt])

    status_notice = (
        f"⚙️ **جارٍ تنفيذ الطلب عبر `agy` CLI** {f'(المحادثة `{target_cid[:8]}`)' if target_cid else '(جلسة جديدة)'}..."
        if lang == "ar"
        else f"⚙️ **Running via headless `agy` CLI** {f'(Conversation `{target_cid[:8]}`)' if target_cid else '(New session)'}..."
    )
    discord_hook.send_discord_rich(token, target_tid, status_notice, is_user_prompt=False, ping_user=False)

    work_dir = get_conversation_project_dir(target_cid)

    # Spawn a background helper process so the listener never blocks while agy runs
    runner_code = f"""
import os, sys, json, time, subprocess
sys.path.insert(0, {repr(CONFIG_DIR)})
import discord_hook, discord_manager

token = {repr(token)}
target_tid = {repr(target_tid)}
target_cid = {repr(target_cid)}
author_id = {repr(author_id)}
cmd = {repr(cmd)}
work_dir = {repr(work_dir)}

before_ids = set(c["id"] for c in discord_manager.list_all_conversations(include_archived=True))
try:
    res = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True, timeout=600)
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
except Exception as e:
    out = ""
    err = str(e)

discord_manager.sync_cli_and_desktop_stores()
after_convs = discord_manager.list_all_conversations(include_archived=True)
resolved_cid = target_cid
if not resolved_cid:
    for c in after_convs:
        if c["id"] not in before_ids:
            resolved_cid = c["id"]
            break

if resolved_cid:
    threads = discord_manager.load_json(discord_manager.THREADS_FILE, {{}})
    threads[resolved_cid] = {{
        "thread_id": target_tid,
        "title": threads.get(resolved_cid, {{}}).get("title") or f"💻 {{resolved_cid[:8]}}",
        "last_author_id": author_id,
    }}
    discord_manager.save_json(discord_manager.THREADS_FILE, threads)
    turns = discord_manager.extract_conversation_turns(resolved_cid)
    if turns:
        max_step = max(t[1] for t in turns if isinstance(t[1], int))
        st = discord_manager.load_json(discord_manager.STATE_FILE, {{}})
        st[resolved_cid] = max_step
        discord_manager.save_json(discord_manager.STATE_FILE, st)

reply_text = out if out else (f"⚠️ CLI finished with no stdout.\\n```\\n{{err[:1500]}}\\n```" if err else "✅ CLI execution completed.")
discord_hook.send_discord_rich(token, target_tid, reply_text, is_user_prompt=False, ping_user=True)
"""
    subprocess.Popen(
        [sys.executable, "-c", runner_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    if source_channel_id != target_tid:
        if lang == "ar":
            return f"🚀 **تم إطلاق جلسة `agy` CLI في الثريد:** <#{target_tid}>"
        return f"🚀 **Started `agy` CLI run in thread:** <#{target_tid}>"
    return None


def run_shell_command(raw_cmd, lang="en"):
    """Executes a quick shell command on the host (`!sh <cmd>` / `!cmd <cmd>`) and returns formatted output."""
    cmd_str = raw_cmd.strip()
    if not cmd_str:
        return "⚠️ **Usage:** `!sh <shell_command>`" if lang == "en" else "⚠️ **الاستخدام:** `!sh <الأمر>`"
    try:
        res = subprocess.run(
            cmd_str,
            shell=True,
            cwd=os.path.expanduser("~"),
            capture_output=True,
            text=True,
            timeout=25,
        )
        combined = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        if not combined:
            combined = "(no output)"
        if len(combined) > 1600:
            combined = combined[:1600] + "\n... (truncated)"
        icon = "✅" if res.returncode == 0 else "⚠️"
        return f"{icon} **`$ {cmd_str}`** (exit `{res.returncode}`):\n```bash\n{combined}\n```"
    except subprocess.TimeoutExpired:
        return f"⏳ **Command timed out after 25s:** `{cmd_str}`"
    except Exception as e:
        return f"⚠️ **Command failed:** `{e}`"


def get_claude_remote_report(arg="", lang="en"):
    """
    Checks or steers Claude Code remote control session.
    - If no arg or 'status': reports Claude Code session status, remote bridge URL, model, and steering instructions.
    - If 'pane' or 'log': shows last 20 lines of tmux pane.
    - If 'start': starts a new claude tmux session if none is active.
    - If 'stop': terminates the claude tmux session.
    - Otherwise: sends prompt text to Claude Code via tmux send-keys.
    """
    ar = lang == "ar"
    tmux_session = "claude-remote"
    res = subprocess.run(["tmux", "has-session", "-t", tmux_session], capture_output=True)
    is_running = (res.returncode == 0)

    active_session = None
    for p in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            with open(p) as f:
                d = json.load(f)
                if d.get("bridgeSessionId") or (d.get("tmux") and tmux_session in d.get("tmux")):
                    active_session = d
                    break
        except Exception:
            pass

    clean_arg = (arg or "").strip()
    norm_arg = clean_arg.lower()

    if norm_arg in ("start", "تشغيل"):
        if is_running:
            return ("⚡ **جلسة كلود كود نشطة بالفعل!** أرسل `!claude` لعرض الرابط والتفاصيل." if ar
                    else "⚡ **Claude Code session is already running!** Send `!claude` to view the link and details.")
        subprocess.run(["tmux", "new-session", "-d", "-s", tmux_session, "cd /home/k/projects/workstation-discord-bridge && claude"])
        time.sleep(2)
        return ("🚀 **تم تشغيل جلسة Claude Code جديدة في الخلفية (`claude-remote`).** أرسل `!claude` لعرض الرابط والتفاصيل." if ar
                else "🚀 **Started a new Claude Code session in tmux (`claude-remote`).** Send `!claude` for status and link.")

    if norm_arg in ("stop", "kill", "ايقاف", "إيقاف", "إنهاء"):
        if not is_running:
            return ("ℹ️ لا توجد جلسة كلود كود نشطة لإيقافها." if ar else "ℹ️ No active Claude Code session to terminate.")
        subprocess.run(["tmux", "kill-session", "-t", tmux_session])
        return ("🛑 **تم إيقاف جلسة Claude Code بنجاح.**" if ar else "🛑 **Claude Code session terminated successfully.**")

    if norm_arg in ("pane", "log", "logs", "شاشة", "سجل"):
        if not is_running:
            return ("⚠️ جلسة كلود كود غير نشطة. أرسل `!claude start` لتشغيلها." if ar else "⚠️ Claude Code is not running. Send `!claude start` to launch.")
        try:
            out = subprocess.run(["tmux", "capture-pane", "-pt", tmux_session, "-S", "-30"], capture_output=True, text=True, timeout=5).stdout
            lines = [l for l in out.splitlines() if l.strip()]
            preview = "\n".join(lines[-18:])
            if ar:
                return f"🖥️ **شاشة Claude Code الحالية (`{tmux_session}`):**\n```text\n{preview}\n```"
            return f"🖥️ **Live Claude Code Pane (`{tmux_session}`):**\n```text\n{preview}\n```"
        except Exception as e:
            return f"⚠️ Error capturing pane: {e}"

    if clean_arg and norm_arg not in ("status", "حالة", "info", "معلومات", "تقرير"):
        if not is_running:
            return ("⚠️ جلسة كلود كود غير نشطة لتمرير الأمر إليها. أرسل `!claude start` أولاً." if ar
                    else "⚠️ Claude Code is not running. Send `!claude start` first.")
        subprocess.run(["tmux", "send-keys", "-t", tmux_session, "-l", clean_arg])
        subprocess.run(["tmux", "send-keys", "-t", tmux_session, "Enter"])
        time.sleep(1.5)
        try:
            out = subprocess.run(["tmux", "capture-pane", "-pt", tmux_session, "-S", "-15"], capture_output=True, text=True, timeout=5).stdout
            lines = [l for l in out.splitlines() if l.strip()]
            preview = "\n".join(lines[-10:])
        except Exception:
            preview = ""
        if ar:
            return (f"📨 **تم إرسال الأمر إلى Claude Code بنجاح:**\n> `{clean_arg}`\n\n"
                    f"🖥️ **المعاينة الفورية للشاشة:**\n```text\n{preview}\n```\n"
                    f"💡 لمتابعة التفاعل الحي افتح تطبيق Claude للهاتف أو الرابط الرسمي.")
        return (f"📨 **Prompt delivered to Claude Code session:**\n> `{clean_arg}`\n\n"
                f"🖥️ **Live Screen Preview:**\n```text\n{preview}\n```\n"
                f"💡 For full streaming interaction, check the Claude mobile app or web session.")

    bridge_id = active_session.get("bridgeSessionId", "") if active_session else ""
    cwd = active_session.get("cwd", "/home/k/projects/workstation-discord-bridge") if active_session else "/home/k/projects/workstation-discord-bridge"
    status_str = active_session.get("status", "active") if active_session else ("running" if is_running else "offline")
    remote_url = f"https://claude.ai/code/{bridge_id}" if bridge_id else "https://claude.ai/code"

    lines = []
    if ar:
        lines.append("🎛️ **التحكم عن بُعد في كلود كود (Claude Code Remote Control)**")
        lines.append("────────────────────────────")
        lines.append("• **الحالة:** `🟢 نشط ومفعل (/rc active)`" if is_running else "• **الحالة:** `⚪ غير مشغّل (أرسل !claude start)`")
        lines.append("• **المحطة المضيفة:** `khalid` (HP EliteBook 840 G6 · Linux)")
        lines.append("• **إصدار كلود:** `Claude Code v2.1.285` (Opus 5.5 xhigh effort)")
        lines.append(f"• **مجلد العمل:** `{cwd}`")
        lines.append(f"• **جلسة tmux:** `{tmux_session}` (`{status_str}`)")
        lines.append("────────────────────────────")
        lines.append("🔗 **رابط التحكم والربط المباشر (تطبيق الهاتف والمتصفح):**")
        lines.append(f"<{remote_url}>")
        lines.append("────────────────────────────")
        lines.append("📱 **التوجيه والتحكم عبر الهاتف:**")
        lines.append("1. **عبر تطبيق Claude (iOS / Android):** تظهر الجلسة تلقائياً في قائمة الجلسات النشطة مع بث TUI حي.")
        lines.append("2. **عبر ديسكورد مباشرة:** أرسل `!claude <أمرك أو سؤالك>` لإرساله فوراً للمحطة.")
        lines.append("3. **معاينة الشاشة:** أرسل `!claude pane` لمشاهدة مخرجات الطرفية الحية.")
    else:
        lines.append("🎛️ **Claude Code Remote Control Center**")
        lines.append("────────────────────────────")
        lines.append("• **Status:** `🟢 Active (/rc active)`" if is_running else "• **Status:** `⚪ Offline (Send !claude start)`")
        lines.append("• **Host Workstation:** `khalid` (HP EliteBook 840 G6 · Linux)")
        lines.append("• **Engine:** `Claude Code v2.1.285` (Opus 5.5 xhigh effort)")
        lines.append(f"• **Working Directory:** `{cwd}`")
        lines.append(f"• **Tmux Session:** `{tmux_session}` (`{status_str}`)")
        lines.append("────────────────────────────")
        lines.append("🔗 **Direct Remote Bridge URL (Mobile App & Web):**")
        lines.append(f"<{remote_url}>")
        lines.append("────────────────────────────")
        lines.append("📱 **Mobile Steering Options:**")
        lines.append("1. **Claude Mobile App (iOS / Android):** Automatically appears in active sessions with full live TUI streaming.")
        lines.append("2. **Direct from Discord:** Send `!claude <prompt>` to pipe commands directly into the terminal.")
        lines.append("3. **Inspect Terminal:** Send `!claude pane` to capture current terminal screen.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Direct Prompt Dispatch via AgentAPI (With Headless CLI Fallback)
# ─────────────────────────────────────────────────────────────────────────────

def has_active_foreground_listener(exclude_pid=None):
    """Checks if an agent-spawned `discord_listener.py` process (other than the systemd daemon) is waiting for stdout."""
    my_pid = os.getpid()
    try:
        out = subprocess.check_output(["pgrep", "-f", "discord_listener.py"], stderr=subprocess.DEVNULL).decode()
        pids = [int(p.strip()) for p in out.split() if p.strip()]
        for p in pids:
            if p == my_pid or (exclude_pid and p == exclude_pid):
                continue
            return True
    except Exception:
        pass
    return False


def is_pro_quota_exhausted():
    """True when every Gemini Argon (agentapi `pro` tier) model reports 0% remaining quota."""
    try:
        us = (ls_rpc("GetUserStatus", {}) or {}).get("userStatus", {})
        cfgs = [c for c in us.get("cascadeModelConfigData", {}).get("clientModelConfigs", [])
                if "argon" in (c.get("modelId") or c.get("label", "")).lower()]
        return bool(cfgs) and all(
            c.get("quotaInfo") is not None and c["quotaInfo"].get("remainingFraction", 0.0) <= 0 for c in cfgs)
    except Exception:
        return False


def dispatch_prompt_via_agentapi(target_conv_id, thread_id, thread_name, author_id, username, full_text, is_new_thread=False):
    """
    Ensures Antigravity is open, then dispatches the incoming Discord prompt via `agentapi`:
    - Matches the system note language to the user's message so English prompts get clean English responses
      and Arabic prompts get Arabic responses!
    - If `target_conv_id` is an existing 36-char UUID conversation, sends the message to it via `agentapi send-message`.
    - Otherwise starts a new conversation via `agentapi new-conversation` and binds `conversation_id` to `thread_id`.
    """
    import discord_hook

    msg_lang = discord_hook.detect_language(full_text)
    if msg_lang == "ar":
        sys_note = f"(ملاحظة للنظام: هذا السؤال مرسل من ديسكورد في الثريد `{thread_name}`. يرجى الرد عليه بوضوح، وسيتم إرسال ردك تلقائياً إلى نفس الثريد في ديسكورد عبر `discord_hook.py`)."
    else:
        sys_note = f"(System note: This message was sent from Discord in thread `{thread_name}`. Please reply in the same language as the user's prompt; your response will be automatically forwarded to this Discord thread via `discord_hook.py`.)"

    formatted_prompt = (
        f"[DISCORD_MESSAGE in {thread_name} (thread_id={thread_id}, author_id={author_id}) from {username}]:\n"
        f"{full_text}\n\n"
        f"{sys_note}"
    )

    clean_title = re.sub(r"^[💬🗄️💻🔀]\s*", "", thread_name).strip()[:60] or "Discord Chat"
    threads = load_json(THREADS_FILE, {})

    if target_conv_id and len(target_conv_id) == 36 and not is_new_thread:
        ok, res = call_agentapi(["send-message", f"--title={clean_title}", target_conv_id, formatted_prompt])
        if ok:
            threads[target_conv_id] = {
                "thread_id": thread_id,
                "title": thread_name[:100],
                "last_author_id": author_id,
            }
            threads["active_thread_id"] = thread_id
            save_json(THREADS_FILE, threads)
            return True, target_conv_id, None

    before_cids = set(c["id"] for c in list_all_conversations(include_archived=True))
    tiers = ["pro", "flash"] if not is_pro_quota_exhausted() else ["flash"]
    ok, res = False, {}
    for i, tier in enumerate(tiers):
        ok, res = call_agentapi(["new-conversation", f"--model={tier}", f"--title={clean_title}", formatted_prompt],
                                retries=3 if i == 0 else 1)
        if ok:
            break

    if ok:
        new_cid = (
            res.get("conversation_id")
            or res.get("conversationId")
            or (res.get("response") or {}).get("conversationId")
            or (res.get("response") or {}).get("cascadeId")
            or ((res.get("response") or {}).get("newConversation") or {}).get("conversationId")
            or ((res.get("response") or {}).get("newConversation") or {}).get("cascadeId")
        )
        if not new_cid:
            time.sleep(1.5)
            after_convs = list_all_conversations(include_archived=True)
            for c in after_convs:
                if c["id"] not in before_cids:
                    new_cid = c["id"]
                    break

        if new_cid:
            if target_conv_id and target_conv_id != new_cid and target_conv_id in threads:
                threads.pop(target_conv_id, None)
            threads[new_cid] = {
                "thread_id": thread_id,
                "title": thread_name[:100],
                "last_author_id": author_id,
            }
            threads["active_thread_id"] = thread_id
            save_json(THREADS_FILE, threads)
            st = load_json(STATE_FILE, {})
            st[new_cid] = 0
            save_json(STATE_FILE, st)
            return True, new_cid, None

    return False, None, str(res.get("error") if isinstance(res, dict) else res)


def get_help_report(lang="en"):
    if lang == "ar":
        return (
            "🤖 **دليل أوامر جسر Antigravity الشامل (سطح المكتب + CLI)**\n"
            "────────────────────────────\n"
            "📚 **استعراض وقراءة المحادثات السابقة (Desktop + CLI):**\n"
            "- `!chats [الصفحة]` (أو `المحادثات`): عرض جميع المحادثات السابقة مع أرقامها ومعرفاتها.\n"
            "- `!cli-chats`: عرض المحادثات التي أُنشئت عبر الطرفية (`agy`).\n"
            "- `!archived` (أو `المؤرشفة`): عرض المحادثات المؤرشفة فقط.\n"
            "- `!search <كلمة>` (أو `بحث <كلمة>`): البحث الفوري في عناوين ومحتوى جميع المحادثات.\n"
            "- `!read <الرقم أو المعرف> [all|N]` (أو `اقرأ <الرقم>`): فتح ثريد مخصص للمحادثة وعرض رسائلها أو كامل سجلها.\n\n"
            "⚡ **التحكم الكامل بالجلسات (Desktop & CLI Power Controls):**\n"
            "- `!sessions` (أو `الجلسات`): لوحة تحكم حية تعرض حالة سطح المكتب، CLI، والجلسات الجارية.\n"
            "- `!cli <سؤالك>`: تشغيل أو استئناف المحادثة مباشرة عبر `agy` في الطرفية.\n"
            "- `!stop [الرقم أو المعرف]` (أو `ايقاف`): إيقاف أي جلسة جارية أو عالقة فوراً.\n"
            "- `!fork [الرقم أو المعرف]` (أو `نسخ`): تفريع/نسخ أي محادثة إلى ثريد جديد مستقل.\n"
            "- `!models` (أو `النماذج`): عرض النماذج المتاحة ونسبة الحصة المتبقية لكل نموذج.\n"
            "- `!claude` (أو `!remote`): التحكم الكامل عن بُعد في كلود كود (رابط الهاتف والمتصفح وتمرير الأوامر).\n"
            "- `!sh <أمر>` (أو `!cmd <أمر>`): تنفيذ أمر طرفية مباشر وعرض النتيجة.\n\n"
            "🗄️ **الأرشفة الثنائية وحالة النظام:**\n"
            "- `!archive [الرقم]` / `!unarchive [الرقم]`: أرشفة أو إلغاء أرشفة المحادثة في الحاسوب وديسكورد معاً.\n"
            "- `!open` (أو `افتح`): فتح تطبيق Antigravity على سطح المكتب إذا كان مغلقاً.\n"
            "- `!status` (أو `الحالة`): تقرير شامل للمعالج، الذاكرة، البطارية، الهاتف، والكتب.\n"
            "- `!crop` (أو `قص`): تقرير تفصيلي لدفعة قص الكتب."
        )
    return (
        "🤖 **Antigravity Discord Bridge — Max-Power Command Reference**\n"
        "────────────────────────────\n"
        "📚 **Conversation History & Search (Desktop + CLI Unified):**\n"
        "- `!chats [page]` — List all conversations across Desktop & CLI with index numbers.\n"
        "- `!cli-chats` — List conversations created in Antigravity CLI (`agy`).\n"
        "- `!archived` — List all archived conversations.\n"
        "- `!search <query>` — Full-text search across all conversation titles, prompts, and replies.\n"
        "- `!read <num|id> [all|N]` — Open a conversation in a dedicated thread and read its summary or full transcript.\n\n"
        "⚡ **Session Control (Desktop & Headless CLI):**\n"
        "- `!sessions` (or `!running`) — Live dashboard of Desktop app, Language Server, CLI, and active cascades.\n"
        "- `!cli [--model=argon|flash|opus|sonnet|gpt|<id>] [--effort=low|medium|high] <prompt>` — Run or continue any session via headless `agy` CLI.\n"
        "- `!stop [num|id]` (or `!cancel`) — Stop/cancel a running or stuck cascade on Desktop or CLI.\n"
        "- `!fork [num|id]` — Fork a conversation into a new branch thread.\n"
        "- `!models` (or `!quota`) — List all AI models with live remaining quota percentages and reset times.\n"
        "- `!claude` (or `!remote`) — Claude Code Remote Control center (direct mobile link, status & tmux steering).\n"
        "- `!sh <command>` (or `!cmd`) — Execute a shell command on the host and return stdout/stderr.\n\n"
        "🗄️ **Two-Way Archive Sync & System Controls:**\n"
        "- `!archive [num|id]` / `!unarchive [num|id]` — Archive or unarchive on both Desktop & Discord.\n"
        "- `!open` — Ensure Antigravity Desktop is open (auto-launches on display `:0` if closed).\n"
        "- `!status` — Full system telemetry (CPU, RAM, thermals, battery, Moto G6, book pipelines).\n"
        "- `!crop` — Live progress report for the book cropping pipeline."
    )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        page = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
        print(format_conversations_list_report(page=page, lang="en"))
    elif cmd == "archived":
        print(format_conversations_list_report(page=1, only_archived=True, lang="en"))
    elif cmd == "search" and len(sys.argv) > 2:
        print(format_search_report(" ".join(sys.argv[2:]), lang="en"))
    elif cmd == "sessions":
        print(get_sessions_report(lang="en"))
    elif cmd == "models":
        print(get_models_report(lang="en"))
    elif cmd == "open":
        ok, ls_addr, csrf, started, msg = ensure_antigravity_open(lang="en")
        print(msg)
    elif cmd == "sync-archives":
        cfg = load_json(CONFIG_FILE, {})
        if cfg.get("bot_token") and cfg.get("channel_id"):
            sync_archives(cfg["bot_token"], cfg["channel_id"], cfg.get("guild_id"))
            print("Archive sync complete.")
