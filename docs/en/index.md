---
title: Workstation Discord Bridge — Overview
description: Mobile bidirectional command center and natural-language Discord bridge for Google Antigravity, Claude Code, and agy CLI.
order: 0
---

**Workstation Discord Bridge** turns Discord on your phone into a direct, conversational mobile interface to a Linux AI engineering workstation.

Read the full multi-page Arabic documentation from the language switcher at the top, or browse the core English guide in the [`README.md`](https://github.com/kmbenjel/workstation-discord-bridge/blob/main/README.md) repository page.

## Key Highlights

- **Zero-Prefix Conversational Threads**: Every post in `#general` spawns a 1:1 thread tied to a persistent Antigravity session; voice notes, screenshots, and `.txt`/`.md`/`.json`/`.py` text attachments are downloaded and injected automatically.
- **Language-Adaptive Bidirectional Formatting (`RTL/LTR`)**: `discord_hook.py` enforces UAX #9 direction isolation, automatically rewrites mismatched Arabic headers above English quotes, separates blockquote header/body paragraphs, and leaves inline/fenced code untouched.
- **Multi-Runtime Steering**: Switch cleanly between Antigravity Desktop IDE (`agentapi`), headless `agy CLI` (`orchestrator`, `argon`, `hydrogen`, `flash`, `opus`, `sonnet`, `gpt`), and Claude Code Remote Control (`!claude` via `tmux`).
- **Archive Search & Telemetry**: Full-text session search (`!search`), session forking (`!fork`), live multi-model quota checks (`!models`), and workstation hardware telemetry (`!status`, `!stop`, `!sh`).
