---
title: التثبيت والإعداد
description: خطوات تهيئة بوت ديسكورد، إعداد ملف discord_config.json، ربط خطاف hooks.json، وتشغيل خدمة systemd.
order: 2
---

يعمل الجسر بالكامل على مكتبة بايثون القياسية (`Python 3.10+`) دون الحاجة إلى تثبيت حزم إضافية للتشغيل الأساسي، ويجعل إعدادُه الخطواتِ التالية واضحة ومباشرة.

## 1. إنشاء بوت ديسكورد وجمع المعرّفات

1. ادخل إلى [Discord Developer Portal](https://discord.com/developers/applications) وأنشئ تطبيقاً جديداً ثم انتقل إلى تبويب **Bot**.
2. فعّل الخيار **Message Content Intent** (ضروري ليتمكّن المستمع من قراءة نص الرسائل الواردة).
3. أنشئ رمز البوت (`Bot Token`) واحفظه بمكان آمن، ثم ادعُ البوت إلى **خادم ديسكورد خاص بك وحدك** مع صلاحيات:
   - `Send Messages` و`Read Message History`
   - `Create Public Threads` و`Send Messages in Threads` و`Manage Threads`
   - `Attach Files` و`Embed Links`
4. فعّل **Developer Mode** في إعدادات ديسكورد لنسخ: `guild_id` (معرّف الخادم)، `channel_id` (معرّف القناة الرئيسية مثل `#general`)، و`user_id` (معرّف حسابك الشخصي).

## 2. نسخ السكربتات وتهيئة الصلاحيات

```bash
git clone https://github.com/kmbenjel/workstation-discord-bridge.git
cd workstation-discord-bridge

mkdir -p ~/.gemini/config
cp discord_listener.py discord_manager.py discord_hook.py discord_send.py discord_status.py ~/.gemini/config/
chmod +x ~/.gemini/config/discord_*.py

cp discord_config.example.json ~/.gemini/config/discord_config.json
chmod 600 ~/.gemini/config/discord_config.json
```

عدّل ملف `~/.gemini/config/discord_config.json`:

```json
{
  "bot_token": "YOUR_DISCORD_BOT_TOKEN",
  "guild_id": "YOUR_SERVER_ID",
  "channel_id": "YOUR_MAIN_CHANNEL_ID",
  "user_id": "YOUR_DISCORD_USER_ID"
}
```

## 3. ربط خطاف الإرسال (`hooks.json`) وقاعدة الاستيقاظ (`GEMINI.md`)

1. أضف خطاف `Stop` إلى `~/.gemini/config/hooks.json` (استبدل `/home/<user>` بمسار منزلك الفعلي لأن ملفات JSON لا توسّع رمز `~`):

```json
{
  "discord-reply-sync": {
    "enabled": true,
    "Stop": [
      {
        "type": "command",
        "command": "/usr/bin/python3 /home/<user>/.gemini/config/discord_hook.py",
        "timeout": 15
      }
    ]
  }
}
```

2. أضف تعليمات الاقتباس وإعادة إطلاق `discord_listener.py` في `~/.gemini/config/GEMINI.md` حتى يتطابق رأس الاقتباس مع لغة السؤال ويُفصل عن السؤال بسطر `>` فارغ، وتبقى الحلقة التفاعلية جاهزة للرسائل التالية.

## 4. تشغيل خدمة الخلفية عبر `systemd`

لتظل المحطة مستجيبةً عبر ديسكورد حتى عند إغلاق جميع الجلسات النشطة:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/discord-listener.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now discord-listener.service

# التحقق من الحالة ومتابعة السجلات الحية
systemctl --user status discord-listener.service
journalctl --user -u discord-listener.service -f
```

تذكّر تفعيل `loginctl enable-linger "$USER"` على لينكس إذا أردت بقاء الخدمة نشطة حتى قبل تسجيل الدخول الرسومي أو بعد انطفاء الشاشة.
