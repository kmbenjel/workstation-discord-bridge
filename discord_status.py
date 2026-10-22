#!/usr/bin/env python3
import os
import re
import sys
import glob
import time
import subprocess
import unicodedata as u

ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def detect_lang(text, default="en"):
    if not text:
        return default
    ar_cnt = len(ARABIC_RE.findall(text))
    en_cnt = len(re.findall(r"[A-Za-z]", text))
    if ar_cnt > en_cnt:
        return "ar"
    if en_cnt > 0:
        return "en"
    return default


PL_DIR = "/home/k/projects/phone-library"


def _ago(ts):
    if not ts:
        return ""
    d = int(time.time() - ts)
    if d >= 86400:
        return f"{d//86400}d {d%86400//3600}h"
    if d >= 3600:
        return f"{d//3600}h {d%3600//60}m"
    return f"{d//60}m"


def get_phone_jobs_summary(lang="en"):
    """Returns a single clean summary line for the main !status report."""
    try:
        units = subprocess.check_output(
            ["systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--plain"],
            text=True, timeout=4
        ).splitlines()
        names = [l.split()[0] for l in units if l.strip()]
        show = subprocess.check_output(
            ["systemctl", "--user", "show", "-p", "Id,WorkingDirectory,ActiveState,SubState"] + names,
            text=True, timeout=4
        )
        active = []
        failed = []
        for blk in show.strip().split("\n\n"):
            kv = dict(l.split("=", 1) for l in blk.splitlines() if "=" in l)
            name = kv.get("Id", "")
            wdir = kv.get("WorkingDirectory", "")
            if not wdir.startswith(PL_DIR) or name.startswith("brave-"):
                continue
            st = kv.get("ActiveState")
            jid = name.removesuffix(".service")
            if st == "active":
                active.append(jid)
            elif st == "failed":
                failed.append(jid)

        try:
            workers = len(subprocess.check_output(["pgrep", "-f", "crop_regress.py"], text=True).split())
        except Exception:
            workers = 0

        ar = lang == "ar"
        if not active and not failed:
            return "لا توجد مهام نشطة" if ar else "No active jobs"

        sample = ", ".join(active[:3])
        if len(active) > 3:
            sample += f" +{len(active)-3}"

        if ar:
            res = f"🟢 **{len(active)} تعمل** (`{sample}`)"
            if workers:
                res += f" | ⚡ {workers} عمال قص"
            if failed:
                res += f" | 🔴 **{len(failed)} متعثرة** (`{', '.join(failed)}`)"
            res += " · `!jobs` للتفاصيل"
        else:
            res = f"🟢 **{len(active)} running** (`{sample}`)"
            if workers:
                res += f" | ⚡ {workers} crop workers"
            if failed:
                res += f" | 🔴 **{len(failed)} failed** (`{', '.join(failed)}`)"
            res += " · `!jobs` for details"
        return res
    except Exception as e:
        return f"Error checking jobs: {e}"


def get_phone_jobs(lang="en", show_logs=False):
    """Clean, categorized report for the dedicated !jobs command."""
    try:
        units = subprocess.check_output(
            ["systemctl", "--user", "list-units", "--all", "--type=service", "--no-legend", "--plain"],
            text=True, timeout=5
        ).splitlines()
        names = [l.split()[0] for l in units if l.strip()]
        show = subprocess.check_output(
            ["systemctl", "--user", "show", "-p", "Id,WorkingDirectory,ExecStart,ActiveState,SubState,ActiveEnterTimestampMonotonic,Result"] + names,
            text=True, timeout=5
        )
        
        daemons = []
        review = []
        processing = []
        failed = []
        
        for blk in show.strip().split("\n\n"):
            kv = dict(l.split("=", 1) for l in blk.splitlines() if "=" in l)
            name = kv.get("Id", "")
            wdir = kv.get("WorkingDirectory", "")
            if not wdir.startswith(PL_DIR) or name.startswith("brave-"):
                continue
            state = kv.get("ActiveState", "")
            sub = kv.get("SubState", "")
            mono = int(kv.get("ActiveEnterTimestampMonotonic") or 0) / 1e6
            up_ts = time.time() - (time.monotonic() - mono) if mono else None
            
            job_id = name.removesuffix(".service")
            item = {"id": job_id, "state": state, "sub": sub, "uptime": _ago(up_ts)}
            
            if state == "failed":
                failed.append(item)
            elif job_id in ("mem-governor", "phone-watchdog", "phone-keepalive", "reporter-follow"):
                daemons.append(item)
            elif "review" in job_id:
                review.append(item)
            else:
                processing.append(item)

        try:
            workers = len(subprocess.check_output(["pgrep", "-f", "crop_regress.py"], text=True).split())
        except Exception:
            workers = 0

        time_now = time.strftime("%H:%M:%S")
        ar = lang == "ar"
        lines = []
        lines.append(f"📋 **مهام المكتبة (phone-library)** ({time_now})" if ar else f"📋 **Phone-Library Jobs ({time_now})**")
        lines.append("────────────────────────────")
        
        if daemons:
            lines.append("🛡️ **خدمات المراقبة والإدارة (Daemons):**" if ar else "🛡️ **Core Daemons & Watchdogs:**")
            for j in sorted(daemons, key=lambda x: x["id"]):
                desc = ""
                if j["id"] == "mem-governor": desc = " · مراقبة الذاكرة" if ar else " · Memory Governor"
                elif j["id"] == "phone-watchdog": desc = " · مراقبة اتصال الهاتف" if ar else " · Phone Connection Watchdog"
                elif j["id"] == "phone-keepalive": desc = " · إبقاء FTP متصلاً" if ar else " · FTP Keepalive"
                lines.append(f"• 🟢 `{j['id']}` — {j['sub']} ({j['uptime']}){desc}")
            lines.append("")

        if review:
            lines.append("📖 **المراجعة وخادم الويب (Review & UI):**" if ar else "📖 **Review & Web UI:**")
            for j in sorted(review, key=lambda x: x["id"]):
                desc = " (:8765)" if "server" in j["id"] else ""
                lines.append(f"• 🟢 `{j['id']}` — {j['sub']} ({j['uptime']}){desc}")
            lines.append("")

        if processing:
            lines.append("✂️ **المعالجة والقص (Processing & Crop):**" if ar else "✂️ **Processing & Crop Tasks:**")
            for j in sorted(processing, key=lambda x: x["id"]):
                extra = f" [{workers} عمال قص نشطين]" if (workers and "crop" in j["id"]) else (f" [{workers} active workers]" if workers else "")
                lines.append(f"• 🟢 `{j['id']}` — {j['sub']} ({j['uptime']}){extra}")
            lines.append("")

        if failed:
            lines.append("⚠️ **مهام متعثرة (Failed):**" if ar else "⚠️ **Failed Jobs:**")
            for j in sorted(failed, key=lambda x: x["id"]):
                lines.append(f"• 🔴 `{j['id']}` — فشلت منذ {j['uptime']}" if ar else f"• 🔴 `{j['id']}` — failed ({j['uptime']} ago)")
            lines.append("")

        lines.append("────────────────────────────")
        return "\n".join(lines).strip()
    except Exception as e:
        return f"⚠️ Error: {e}"


def get_status_report(lang="en"):
    # 1. Instant CPU % & loadavg
    try:
        cpus = os.cpu_count() or 1
        def _read_cpu_ticks():
            with open("/proc/stat") as sf:
                for line in sf:
                    if line.startswith("cpu "):
                        v = [float(x) for x in line.split()[1:]]
                        return (v[3] + v[4]), sum(v)
            return 0, 0
        i1, t1 = _read_cpu_ticks()
        time.sleep(0.12)
        i2, t2 = _read_cpu_ticks()
        dt, di = (t2 - t1), (i2 - i1)
        cpu_pct = int(max(0.0, min(100.0, (1.0 - di / dt) * 100))) if dt > 0 else 0

        with open("/proc/loadavg") as f:
            parts = f.read().split()[:3]
            l1, l5, l15 = float(parts[0]), float(parts[1]), float(parts[2])
            if lang == "ar":
                load = f"{cpu_pct}% استهلاك فوري (الضغط: {l1:.1f}/{cpus} أنوية · 1د: {l1:.2f} | 5د: {l5:.2f})"
            else:
                load = f"{cpu_pct}% instant (load: {l1:.1f}/{cpus} cores · 1m: {l1:.2f} | 5m: {l5:.2f})"
    except Exception:
        load = "?"

    # 2. RAM & Swap
    mem_str = "?"
    try:
        out = subprocess.check_output(["free", "-m"]).decode()
        for line in out.splitlines():
            if line.startswith("Mem:"):
                p = line.split()
                mem_str = f"{float(p[2])/1024:.1f}/{float(p[1])/1024:.1f} GB"
            elif line.startswith("Swap:"):
                p = line.split()
                mem_str += f", swap {float(p[2])/1024:.1f} GB"
    except Exception:
        pass

    # 3. Disk Free
    try:
        disk_free = subprocess.check_output(["df", "-h", "--output=avail", "/"]).decode().splitlines()[-1].strip()
    except Exception:
        disk_free = "?"

    # 4. Thermal
    temp_str = ""
    try:
        temps = [int(open(p).read()) / 1000 for p in glob.glob("/sys/class/thermal/thermal_zone*/temp")]
        if temps:
            temp_str = f"{int(max(temps))}°C"
    except Exception:
        pass

    # 5. Battery
    bat_str = ""
    try:
        bats = glob.glob("/sys/class/power_supply/BAT*")
        if bats:
            cap = open(os.path.join(bats[0], "capacity")).read().strip()
            stat = open(os.path.join(bats[0], "status")).read().strip()
            bat_str = f"{cap}% ({stat})"
    except Exception:
        pass

    # 6. Top CPU Processes
    top_str = ""
    try:
        top_lines = subprocess.check_output(["ps", "-eo", "pcpu,comm", "--sort=-pcpu"]).decode().splitlines()
        top_items = []
        for l in top_lines[1:4]:
            p = l.strip().split()
            if len(p) >= 2 and p[1] != "ps":
                top_items.append(f"{p[1]} {int(float(p[0]))}%")
        top_str = " | ".join(top_items)
    except Exception:
        pass

    # 7. Antigravity Desktop & CLI Status
    agy_desk = subprocess.run(["pgrep", "-f", "/opt/Antigravity/antigravity"], stdout=subprocess.PIPE).stdout.decode().strip()
    agy_ls = subprocess.run(["pgrep", "-f", "resources/bin/language_server"], stdout=subprocess.PIPE).stdout.decode().strip()
    agy_cli = subprocess.run(["pgrep", "-x", "agy"], stdout=subprocess.PIPE).stdout.decode().strip()

    # 8. Phone FTP Status
    phone_host = "192.168.11.100"
    cur_p = "/home/k/projects/phone-library/data/phone/current-address.txt"
    if os.path.exists(cur_p):
        try:
            phone_host = open(cur_p).read().strip() or phone_host
        except Exception:
            pass
    code = subprocess.run(["nc", "-z", "-w2", phone_host, "2221"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode

    # 9. Crop Batch Status
    crop_en = "No active batch"
    crop_ar = "لا توجد دفعات نشطة"
    try:
        batches = sorted(glob.glob("/home/k/projects/phone-library/data/crop/batch-*.txt"), key=os.path.getmtime, reverse=True)
        if batches:
            b = batches[0]
            bname = os.path.basename(b).replace(".txt", "")
            lines = [l.strip() for l in open(b, encoding="utf-8") if l.strip()]
            total_b = len(lines)
            done_p = "/home/k/projects/phone-library/data/crop/done.tsv"
            done_set = set()
            if os.path.exists(done_p):
                for l in open(done_p, encoding="utf-8"):
                    parts = l.split("\t")
                    if parts:
                        done_set.add(u.normalize("NFC", parts[0].strip().removeprefix("Books/")))
            done_cnt = sum(1 for l in lines if u.normalize("NFC", l.split("\t")[0].strip().removeprefix("Books/")) in done_set)
            p_stat = subprocess.run(["pgrep", "-f", "[c]rop_batch.py"], stdout=subprocess.PIPE).stdout.decode().strip()
            crop_en = f"{bname}: {done_cnt}/{total_b} ({'running' if p_stat else 'idle'})"
            crop_ar = f"{bname}: {done_cnt}/{total_b} ({'قيد المعالجة' if p_stat else 'متوقف'})"
    except Exception as e:
        crop_en = crop_ar = str(e)

    time_now = time.strftime("%H:%M:%S")

    if lang == "ar":
        phone_str = f"متصل ({phone_host}:2221)" if code == 0 else f"غير متصل ({phone_host})"
        agy_str = f"سطح المكتب: {'يعمل 🟢' if agy_desk else 'مغلق 🔴'} | خادم اللغة: {'متصل' if agy_ls else 'متوقف'}"
        if agy_cli:
            agy_str += f" | CLI: نشط"
        return (
            f"📊 **تقرير حالة النظام و Antigravity ({time_now})**\n"
            f"────────────────────────────\n"
            f"- **تطبيق Antigravity**: `{agy_str}`\n"
            f"- **المعالج (CPU)**: `{load}`\n"
            f"- **الذاكرة (RAM)**: `{mem_str}`\n"
            f"- **المساحة الشاغرة (Disk)**: `{disk_free}`"
            f"{f' | **الحرارة**: `{temp_str}`' if temp_str else ''}"
            f"{f' | **البطارية**: `{bat_str}`' if bat_str else ''}\n"
            f"- **أعلى العمليات**: `{top_str}`\n"
            f"- **هاتف المكتبة**: `{phone_str}`\n"
            f"- **دفعة القص (Crop)**: `{crop_ar}`\n"
            f"- **مهام المكتبة (phone-library)**: {get_phone_jobs_summary('ar')}"
        )

    phone_str = f"Online ({phone_host}:2221)" if code == 0 else f"Offline ({phone_host})"
    agy_str = f"Desktop: {'Running 🟢' if agy_desk else 'Closed 🔴'} | LS: {'Ready' if agy_ls else 'Down'}"
    if agy_cli:
        agy_str += " | CLI: Active"
    return (
        f"📊 **System & Antigravity Status ({time_now})**\n"
        f"────────────────────────────\n"
        f"- **Antigravity**: `{agy_str}`\n"
        f"- **CPU Load**: `{load}`\n"
        f"- **RAM / Swap**: `{mem_str}`\n"
        f"- **Disk Free**: `{disk_free}`"
        f"{f' | **Temp**: `{temp_str}`' if temp_str else ''}"
        f"{f' | **Battery**: `{bat_str}`' if bat_str else ''}\n"
        f"- **Top Processes**: `{top_str}`\n"
        f"- **Phone FTP**: `{phone_str}`\n"
        f"- **Crop Batch**: `{crop_en}`\n"
        f"- **phone-library jobs**: {get_phone_jobs_summary('en')}"
    )


def get_crop_report(lang="en"):
    batches = sorted(glob.glob("/home/k/projects/phone-library/data/crop/batch-*.txt"), key=os.path.getmtime, reverse=True)
    if not batches:
        return "⚠️ لا توجد دفعات قص مسجلة في `data/crop/`." if lang == "ar" else "⚠️ No crop batches found in `data/crop/`."
    b = batches[0]
    bname = os.path.basename(b).replace(".txt", "")
    lines = [l.strip() for l in open(b, encoding="utf-8") if l.strip()]
    total_b = len(lines)
    done_p = "/home/k/projects/phone-library/data/crop/done.tsv"
    done_set = set()
    if os.path.exists(done_p):
        for l in open(done_p, encoding="utf-8"):
            parts = l.split("\t")
            if parts:
                done_set.add(u.normalize("NFC", parts[0].strip().removeprefix("Books/")))
    done_cnt = sum(1 for l in lines if u.normalize("NFC", l.split("\t")[0].strip().removeprefix("Books/")) in done_set)
    rem = total_b - done_cnt
    pct = (done_cnt / total_b * 100) if total_b else 0

    p_pids = subprocess.run(["pgrep", "-f", "[c]rop_batch.py"], stdout=subprocess.PIPE).stdout.decode().split()
    workers = len(p_pids) - 1 if len(p_pids) > 1 else len(p_pids)

    log_p = f"/home/k/projects/phone-library/logs/{bname}.log"
    last_log = ""
    if os.path.exists(log_p):
        try:
            tail = subprocess.check_output(["tail", "-n", "5", log_p]).decode(errors="ignore").strip().splitlines()
            for l in reversed(tail):
                if "CROPPED" in l or "DONE" in l or "FAIL" in l:
                    last_log = l.strip()
                    break
        except Exception:
            pass

    if lang == "ar":
        state_label = f"نشط ({workers} workers)" if p_pids else "متوقف مؤقتاً"
        return (
            f"🌾 **حالة دفعة القص الحالية (`{bname}`)**\n"
            f"────────────────────────────\n"
            f"- **الحالة البرمجية**: `{state_label}`\n"
            f"- **المنجز**: **{done_cnt}** من أصل **{total_b}** كتاب ({pct:.1f}%)\n"
            f"- **المتبقي**: **{rem}** كتاب\n"
            f"{f'- **آخر معالجة**: `{last_log}`' if last_log else ''}"
        )

    state_label = f"Running ({workers} workers)" if p_pids else "Paused / Idle"
    return (
        f"🌾 **Current Crop Batch Status (`{bname}`)**\n"
        f"────────────────────────────\n"
        f"- **State**: `{state_label}`\n"
        f"- **Completed**: **{done_cnt}** / **{total_b}** books ({pct:.1f}%)\n"
        f"- **Remaining**: **{rem}** books\n"
        f"{f'- **Last Log**: `{last_log}`' if last_log else ''}"
    )


if __name__ == "__main__":
    arg_lang = sys.argv[1] if len(sys.argv) > 1 else "en"
    print(get_status_report(arg_lang))
    print()
    print(get_crop_report(arg_lang))
