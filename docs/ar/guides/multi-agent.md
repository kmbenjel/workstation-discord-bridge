---
title: قيادة الوكلاء المتعددين وتبديل النماذج
description: التحكّم في Antigravity Desktop وagy CLI وجلسات Claude Code Remote Control ومتابعة الحصص.
order: 2
---

لا يقيّدك الجسر بوكيل واحد أو بيئة تشغيل واحدة؛ بل يضع بين يديك طبقة قيادة موحّدة لـ `Antigravity Desktop IDE` و`agy CLI` و`Claude Code Native`.

<p class="shot">
  <img src="/screenshots/claude_remote_control.png" alt="لوحة تحكم Claude Code Remote Control" width="380">
  <img src="/screenshots/ai_models_quota_check.png" alt="مراقب حصص النماذج الحية" width="380">
</p>

## 1. جسر `Claude Code Remote Control` (`!claude`)

إذا كنت تشغّل جلسة `Claude Code` داخل `tmux` (باسم الجلسة `claude-remote`) مع ميزة `/rc`:
- أرسل `!claude` أو `!remote` للحصول على حالة الجلسة والنموذج العامل ومجلّد العمل و**رابط الجلسة المباشر لتطبيق الهاتف (`https://claude.ai/code/session_...`)**.
- أرسل `!claude pane` لالتقاط صورة نصية حية لآخر أسطر نافذة `tmux`.
- أرسل `!claude <أمر أو سؤال>` لتوجيه الأمر مباشرة إلى نافذة الطرفية النشطة دون الحاجة لفتح التطبيق الآخر.

## 2. التشغيل الصامت وتوحيد الجلسات (`!cli` و`!fork`)

بفضل دالة `sync_cli_and_desktop_stores()` في `discord_manager.py`، تُربط مخازن `~/.gemini/antigravity` و`~/.gemini/antigravity-cli` معاً:
- يمكنك متابعة أي محادثة بدأت في محرر سطح المكتب عبر الطرفية الصامتة بإرسال:
  ```text
  !cli --model=orchestrator --effort=high راجع التعديلات وأكمل الاختبارات
  ```
- إذا نفدت حصة النموذج الافتراضي أو رغبت في استكشاف مسار مختلف دون تغيير الجلسة الأصلية، أرسل `!fork` لإنشاء خيط متفرّع مستقل (`ForkConversation RPC`).

## 3. النماذج المدعومة ومراقبة الحصص (`!models`)

يدعم الجسر التبديل السريع عبر الأسماء المختصرة الآتية (تُحوَّل في `resolve_model_alias`):

| الاسم المختصر | المعرّف الفعلي للوكيل | الاستخدام الأمثل |
|---|---|---|
| `orchestrator` / `orch` / `pro` | `gemini-pro-orchestrator-g1-pro` | النموذج الافتراضي للمهام الهندسية والتنسيق متعدد الخطوات |
| `argon` | `gemini-4-argon-high` | التفكير العميق والتحليل المعماري المعقد |
| `hydrogen` | `gemini-4-hydrogen-high` | مهام البحث والتحرير المتقدّمة |
| `flash` / `fast` | `gemini-3.8-flash-medium` / `low` | الاستجابات السريعة والاحتياط التلقائي عند نفاد الحصة |
| `opus` / `claude` | `claude-opus-5-5-high` | التعديل البرمجي الدقيق عبر عائلة كلود |
| `sonnet` | `claude-sonnet-5-5-medium` | التوازن بين السرعة وجودة الشيفرة |
| `gpt` / `oss` | `gpt-oss-120b-medium` | الاستدلال البديل عبر مجموعة `GPT-OSS` |

عند إرسال `!models` أو `!quota`، يستعلم الجسر من خادم اللغة المحلي (`GetUserStatus`) ليعرض جدول النسب المئوية المتبقية ومجموعات الحصص المشتركة وموعد التصفير القادم بتوقيت `UTC`.
