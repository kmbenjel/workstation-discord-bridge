# Workstation Discord Bridge

<div align="center">

**Two-Way Autonomous Agent Steering, Mobile Supervision & Claude Code Remote Control**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Linux](https://img.shields.io/badge/Linux-Workstation-fcc624?style=for-the-badge&logo=linux&logoColor=black)](https://kernel.org)
[![Discord](https://img.shields.io/badge/Discord-Gateway%20v10-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com)
[![Antigravity](https://img.shields.io/badge/Antigravity-Desktop%20App-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://github.com/google)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Remote%20Control%20(/rc)-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://claude.ai)

</div>

---

## Overview

**Workstation Discord Bridge** connects a Linux development workstation to Discord for fast, bidirectional mobile supervision and steering. It allows you to supervise autonomous AI coding sessions (Google Antigravity desktop app, Claude Code Native), monitor running background jobs, run shell commands, and inspect workstation resources directly from a phone without needing remote desktop software.

- **Desktop ➔ Mobile**: Prompts executed in the Antigravity desktop app automatically stream structured turn completions, code diffs, and test results straight to your phone via lifecycle hooks.
- **Mobile ➔ Workstation**: Send commands or prompts from Discord mobile (`!claude`, `!models`, `!search`, `!status`, `!sh`) to control host runtimes, check quota limits, or steer background agents on the fly.

---

## Architecture & Data Flow

The bridge decouples **inbound mobile commands** (polled via a lightweight background listener) from **outbound agent responses** (pushed automatically on turn completion via lifecycle hooks):

```mermaid
flowchart TB
    subgraph Mobile ["📱 Mobile Device (Discord App)"]
        UserPrompt["User Prompt / Command (!claude, !models, !search, Prompt)"]
        MobileThread["Active Synchronized Conversation Thread"]
    end

    subgraph DiscordCloud ["☁️ Discord Cloud Gateway (v10 API)"]
        Gateway["Channels & Threads API"]
    end

    subgraph Workstation ["💻 Linux Workstation (khalid)"]
        subgraph InboundPipeline ["Inbound Command & Dispatch Loop"]
            Listener["discord_listener.py\n(Sub-second Poller & Router)"]
            Manager["discord_manager.py\n(Session & Process Controller)"]
        end

        subgraph CoreRuntimes ["Execution Runtimes"]
            AntigravityLS["Antigravity Language Server\n(Port + CSRF Auto-Discovery)"]
            AgyCLI["Antigravity CLI (agy)\nHeadless Agent Sessions"]
            ClaudeRemote["Claude Code Native\n(tmux: claude-remote /rc active)"]
            ShellWorker["Subprocess Worker\n(System Commands & Diagnostics)"]
        end

        subgraph OutboundPipeline ["Outbound Event & Reply Hook"]
            Hook["discord_hook.py\n(Antigravity Turn Hook)"]
            StatusGen["discord_status.py\n(Hardware & Pipeline Inspector)"]
        end

        subgraph Storage ["Persistent State"]
            Brain["~/.gemini/antigravity/brain/\n(Transcripts & Artifacts)"]
            ThreadsJSON["discord_threads.json\n(Active Thread Sync Map)"]
        end
    end

    %% Inbound Flow
    UserPrompt -->|Send message| Gateway
    Gateway -->|Poll / Long-wait| Listener
    Listener -->|Route action| Manager
    Manager -->|agentapi / IPC| AntigravityLS
    Manager -->|Spawn / Attach| AgyCLI
    Manager -->|tmux send-keys| ClaudeRemote
    Manager -->|Exec| ShellWorker
    Manager -->|Query state| StatusGen

    %% Outbound Flow
    AntigravityLS -->|Write state| Brain
    AgyCLI -->|Write state| Brain
    ClaudeRemote -->|TUI Socket / Web Bridge| Gateway
    Brain -->|Trigger Stop hook| Hook
    Hook -->|Read thread map| ThreadsJSON
    Hook -->|Post structured response| Gateway
    Gateway -->|Push notification| MobileThread
    StatusGen -->|Format markdown| Hook
```

---

## Key Features

### 1. Claude Code Native Remote Control (`!claude` / `!remote`)
- **Direct Remote Session Hook**: Connects to Claude Code's native remote session (`/rc active`) running inside a background tmux session.
- **One-Tap Mobile Handoff**: Posts the live session URL (`https://claude.ai/code/session_...`) for immediate interactive handoff to the Claude iOS/Android app.
- **Terminal Mirroring**: Inspect live terminal outputs directly inside Discord using `!claude pane`.
- **Bidirectional Prompting**: Send `!claude <prompt>` from Discord to pipe instructions straight to Claude Code.

### 2. Multi-Model Quota Monitor (`!models` / `!quota`)
- **Dynamic Language Server Queries**: Queries the local Antigravity Language Server to report remaining quotas and reset schedules.
- **Shared Pool Grouping**: Automatically handles shared quota pools across Gemini 3.8 Flash, Opus 5.5, Sonnet 5.5, and Argon.
- **Fast Response**: Returns accurate usage metrics in under a second directly to your phone.

### 3. Session Archive Search (`!search`)
- **Thread Sync**: Keeps active conversations mapped to clear, navigable Discord threads.
- **Full-Text Archive Search**: Run `!search <query>` to instantly search past sessions, turn histories, and code discussions without opening the desktop app.

### 4. Workstation Telemetry & Process Management (`!status`, `!stop`, `!running`)
- **Host Diagnostics**: Live CPU utilization, memory and swap usage, thermals, battery state, and disk space.
- **Process Supervision**: List active cascades and background tasks (`!running`) and stop runaway processes instantly (`!stop`).
- **Direct Shell Access**: Execute system commands (`!sh <cmd>`) with clean terminal output formatting.

---

## Mobile Command Reference

Commands sent from Discord mobile to supervise and control the host workstation:

| Command | Action & Output |
|---|---|
| `!claude` / `!remote` | Claude Code remote control status, active model, mobile app handoff URL, and working directory. |
| `!claude pane` | Capture and display the live tmux terminal mirror from Claude Code. |
| `!claude <prompt>` | Pipe a command or instruction directly into the active Claude Code session. |
| `!models` / `!quota` | Quota monitor: remaining percentages, shared pools, and UTC reset times. |
| `!search <query>` | Instant full-text search across previous desktop sessions and turn history. |
| `!status` | Host workstation telemetry: CPU load, RAM usage, thermals, battery, and disk. |
| `!running` / `!sessions` | Control center showing running background tasks, active cascades, and CLI jobs. |
| `!stop` / `!cancel` | Terminate background tasks or runaway loops immediately. |
| `!fork` | Fork the current conversation into a new branch thread for isolated exploration. |
| `!sh <command>` | Execute host bash commands with formatted terminal output. |
| `!cli <prompt>` | Launch a headless `agy` CLI turn in the background and stream the output to Discord. |
| `!chats [page]` | Paginated catalog of past conversations. |

---

## 📱 Mobile Supervision Showcase

Screenshots captured from the mobile Discord client during live workstation sessions:

<div align="center">

| 🎛️ Claude Code Remote Control (`!claude`) | 🧠 AI Models Quota Monitor (`!models`) |
|:---:|:---:|
| <img src="docs/screenshots/claude_remote_control.png" width="370" alt="Claude Code Remote Control"> <br> *Direct bridge URL for Claude iOS/Android app, working directory, and live steering* | <img src="docs/screenshots/ai_models_quota_check.png" width="370" alt="AI Models Quota Monitor"> <br> *Quota percentage check across Gemini 3.8 Flash, Opus 5.5, Sonnet 5.5, and Argon* |

<br>

| 📊 Workstation Telemetry (`!status`) | 🔍 Conversation Archive Search (`!search`) |
|:---:|:---:|
| <img src="docs/screenshots/live_telemetry_check.png" width="370" alt="Workstation Telemetry"> <br> *Host hardware metrics: CPU load, RAM and swap, thermals, and battery level* | <img src="docs/screenshots/conversation_archive_search.png" width="370" alt="Conversation Archive Search"> <br> *Instant full-text query across past session titles and turns with direct thread links* |

<br>

| 📱 Active Session Controller (`!running`) | 🛑 Emergency Process Cancellation (`!stop`) |
|:---:|:---:|
| <img src="docs/screenshots/mobile_agent_steering.png" width="370" alt="Active Session Controller"> <br> *Overview of active cascades, background tasks, and language server status* | <img src="docs/screenshots/emergency_process_cancellation.png" width="370" alt="Emergency Process Cancellation"> <br> *Instant termination of runaway tasks and background workers via `!stop`* |

<br>

| 💻 Direct Shell Execution (`!sh`) | 📨 Automated Turn Completion Push |
|:---:|:---:|
| <img src="docs/screenshots/raw_shell_execution.png" width="370" alt="Direct Shell Execution"> <br> *Direct host shell command execution returned with formatted terminal styling* | <img src="docs/screenshots/agent_turn_completion.png" width="370" alt="Automated Turn Completion Push"> <br> *Automated turn verification and execution results pushed via lifecycle hook* |

</div>

---

## Repository Structure

```
workstation-discord-bridge/
├── discord_manager.py          # Session controller, Claude remote bridge, and quota manager
├── discord_listener.py         # Sub-second message poller, thread router, and command dispatcher
├── discord_hook.py             # Antigravity turn hook: formats & transmits responses to Discord
├── discord_status.py           # Hardware, systemd, and pipeline telemetry collector
├── discord_send.py             # Standalone CLI utility to send messages to Discord
├── hooks.json.example          # Sample Antigravity hooks configuration
├── discord_config.example.json # Configuration schema template
├── requirements.txt            # Python dependencies
├── docs/
│   └── screenshots/            # Curated mobile captures
├── systemd/
│   └── discord-listener.service# Systemd user service unit for background operation
└── README.md                   # Project documentation
```

---

## Setup & Installation

### 1. Prerequisites
- Linux Workstation (Ubuntu / Debian / Fedora)
- Python 3.10+
- A Discord Bot with Message Content Intent enabled ([Discord Developer Portal](https://discord.com/developers))
- Claude Code Native (`npm install -g @anthropic-ai/claude-code`) or Google Antigravity

### 2. Configuration
Clone the repository and prepare your private configuration:
```bash
git clone https://github.com/kmbenjel/workstation-discord-bridge.git
cd workstation-discord-bridge

# Copy the example config
cp discord_config.example.json discord_config.json
chmod 600 discord_config.json
```

Edit `discord_config.json`:
```json
{
  "bot_token": "YOUR_DISCORD_BOT_TOKEN",
  "guild_id": "YOUR_SERVER_ID",
  "channel_id": "YOUR_MAIN_CHANNEL_ID",
  "user_id": "YOUR_DISCORD_USER_ID"
}
```

### 3. Antigravity Hook Integration
Add the hook to your Antigravity hooks file (`~/.gemini/config/hooks.json`):
```json
{
  "discord-reply-sync": {
    "enabled": true,
    "Stop": [
      {
        "type": "command",
        "command": "/usr/bin/python3 /path/to/workstation-discord-bridge/discord_hook.py",
        "timeout": 15
      }
    ]
  }
}
```

### 4. Running as a Background Systemd Service
To keep the listener running continuously:
```bash
# Copy and enable the service
mkdir -p ~/.config/systemd/user
cp systemd/discord-listener.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now discord-listener.service

# Check live logs
journalctl --user -u discord-listener.service -f
```

---

## License & Credits

Built by [Khalid Benjelloun](https://benjelloun.dev) with [Google Antigravity](https://github.com/google) and [Claude Code](https://claude.ai). MIT License.
