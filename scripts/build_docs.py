#!/usr/bin/env python3
"""Build static RTL Arabic documentation site (+ English overview) for bridge.benjelloun.dev."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

import markdown
import yaml
from jinja2 import Environment, select_autoescape
from markdown.extensions import Extension
from markdown.extensions.toc import slugify_unicode
from markdown.treeprocessors import Treeprocessor

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "_site"
SITE_URL = "https://bridge.benjelloun.dev"
CNAME = "bridge.benjelloun.dev"
REPO_URL = "https://github.com/kmbenjel/workstation-discord-bridge"

_LTR_SCRIPT = re.compile(r"^[\x00-\u02ff\u2000-\u206f]*$")

ORDER_MAP = {
    "index.md": 0,
    "why.md": 1,
    "install.md": 2,
    "quickstart.md": 3,
    "guides/direct-chat.md": 4,
    "guides/multi-agent.md": 5,
    "guides/commands.md": 6,
    "faq.md": 9,
}


@dataclass
class DocPage:
    lang: str
    rel: str
    url: str
    title: str
    description: str
    order: int
    body: str
    html: str = ""
    toc: list = field(default_factory=list)


class _RtlCodeTreeprocessor(Treeprocessor):
    def run(self, root: ET.Element) -> None:
        tables = [(parent, i, el) for parent in root.iter() for i, el in enumerate(parent) if el.tag == "table"]
        for parent, i, el in reversed(tables):
            wrap = ET.Element("div", {"class": "table-wrap"})
            parent.remove(el)
            wrap.append(el)
            parent.insert(i, wrap)
        for el in root.iter():
            if el.tag == "a" and (el.get("href", "").startswith(("http://", "https://"))):
                el.set("rel", "noopener")
            elif el.tag == "img":
                el.set("loading", "lazy")
                el.set("decoding", "async")
            elif el.tag == "code" and (el.text or "") and _LTR_SCRIPT.match(el.text or ""):
                el.set("dir", "ltr")


class _RtlExtension(Extension):
    def extendMarkdown(self, md):  # noqa: N802
        md.treeprocessors.register(_RtlCodeTreeprocessor(md), "bridge_rtl", 1)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return " ".join("".join(p.parts).split())


def split_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            return meta, "\n".join(lines[i + 1 :]).lstrip("\n")
    return {}, text


def url_for(lang: str, rel: str) -> str:
    stem = rel.removesuffix(".md")
    if stem == "index":
        stem = ""
    parts = [p for p in ("en" if lang == "en" else "", stem) if p]
    return "/" + "/".join(parts) + ("/" if parts else "")


def render_md(body: str) -> tuple[str, list]:
    md = markdown.Markdown(
        extensions=["extra", "sane_lists", "toc", _RtlExtension()],
        extension_configs={
            "toc": {
                "slugify": slugify_unicode,
                "permalink": "#",
                "permalink_class": "anchor",
                "permalink_title": "رابط هذا القسم",
                "toc_depth": "2-3",
            }
        },
        output_format="html",
    )
    html = md.convert(body)
    html = re.sub(r'<pre><code class="language-([\w-]+)">', r'<pre dir="ltr"><code class="language-\1">', html)
    html = html.replace("<pre><code>", '<pre dir="ltr"><code>')
    return html, md.toc_tokens


BASE_TMPL = """{% set is_ar = (lang == "ar") -%}
<!doctype html>
<html lang="{{ lang }}" dir="{{ 'rtl' if is_ar else 'ltr' }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{{ page.title }} · {{ 'جسر ديسكورد لمحطة العمل' if is_ar else 'Workstation Discord Bridge' }}</title>
<meta name="description" content="{{ page.description }}">
<link rel="canonical" href="{{ canonical }}">
<link rel="icon" href="/assets/img/favicon.svg" type="image/svg+xml">
<link rel="preload" href="/assets/fonts/readex-pro-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/css/site.css?v={{ ver }}">
<script>try{var t=localStorage.getItem("warraq-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}</script>
</head>
<body class="doc has-nav{% if page.toc and page.toc|length > 1 %} has-toc{% endif %}">
<a class="skip" href="#main">{{ 'انتقل إلى المحتوى' if is_ar else 'Skip to content' }}</a>
<header class="top">
  <div class="top-in">
    <a class="brand" href="{{ '/' if is_ar else '/en/' }}"><span>{{ 'جسر ديسكورد لمحطة العمل' if is_ar else 'Workstation Discord Bridge' }}</span></a>
    <nav class="top-nav" aria-label="{{ 'القائمة' if is_ar else 'Menu' }}">
      <a href="/">{{ 'نظرة عامة' if is_ar else 'Overview' }}</a>
      <a href="/quickstart/">{{ 'البدء السريع' if is_ar else 'Quick Start' }}</a>
      <a href="/guides/commands/">{{ 'مرجع الأوامر' if is_ar else 'Commands' }}</a>
      <a href="/faq/">{{ 'الأسئلة الشائعة' if is_ar else 'FAQ' }}</a>
    </nav>
    <div class="top-tools">
      <button class="icon-btn" type="button" data-search-open aria-label="{{ 'بحث' if is_ar else 'Search' }}" title="{{ 'بحث' if is_ar else 'Search' }} ( / )">🔍</button>
      <button class="icon-btn" type="button" data-theme-toggle aria-label="{{ 'تبديل السمة' if is_ar else 'Toggle theme' }}" title="{{ 'تبديل السمة' if is_ar else 'Toggle theme' }}">◐</button>
      <a class="lang" href="{{ '/en/' if is_ar else '/' }}" hreflang="{{ 'en' if is_ar else 'ar' }}">{{ 'English' if is_ar else 'العربية' }}</a>
      <a class="icon-btn" href="{{ repo_url }}" aria-label="GitHub" rel="noopener">GitHub</a>
    </div>
  </div>
</header>

<div class="doc-grid">
  <aside class="side" aria-label="{{ 'الأقسام' if is_ar else 'Sections' }}">
    <details class="side-box" data-side open>
      <summary><span>{{ 'دليل الاستخدام' if is_ar else 'Documentation' }}</span></summary>
      <nav class="side-nav">
        <ul>
        {% for item in nav_top %}
          <li><a href="{{ item.url }}"{% if item.url == page.url %} aria-current="page"{% endif %}>{{ item.title }}</a></li>
        {% endfor %}
          <li class="group">
            <details open>
              <summary>{{ 'الأدلة التفصيلية' if is_ar else 'Guides' }}</summary>
              <ul>
              {% for g in nav_guides %}
                <li><a href="{{ g.url }}"{% if g.url == page.url %} aria-current="page"{% endif %}>{{ g.title }}</a></li>
              {% endfor %}
              </ul>
            </details>
          </li>
        {% for item in nav_bottom %}
          <li><a href="{{ item.url }}"{% if item.url == page.url %} aria-current="page"{% endif %}>{{ item.title }}</a></li>
        {% endfor %}
        </ul>
      </nav>
      <p class="side-label">{{ 'روابط سريعة' if is_ar else 'Links' }}</p>
      <ul class="side-tools">
        <li><a href="{{ repo_url }}" rel="noopener">GitHub Repository</a></li>
        <li><a href="https://warraq.benjelloun.dev" rel="noopener">ورّاق · Warraq</a></li>
        <li><a href="https://benjelloun.dev" rel="noopener">benjelloun.dev</a></li>
      </ul>
    </details>
  </aside>

  <main id="main" class="content">
    <p class="crumb"><a href="/">{{ 'الرئيسية' if is_ar else 'Home' }}</a> <span aria-hidden="true">›</span> <span>{{ page.title }}</span></p>
    <article class="prose">
      <h1>{{ page.title }}</h1>
      {% if page.description %}<p class="lead">{{ page.description }}</p>{% endif %}
      {{ page.html|safe }}
    </article>
    {% if prev_p or next_p %}
    <nav class="pager" aria-label="التنقل بين الصفحات">
      {% if prev_p %}<a class="prev" href="{{ prev_p.url }}"><small>{{ 'السابق' if is_ar else 'Previous' }}</small>{{ prev_p.title }}</a>{% else %}<span></span>{% endif %}
      {% if next_p %}<a class="next" href="{{ next_p.url }}"><small>{{ 'التالي' if is_ar else 'Next' }}</small>{{ next_p.title }}</a>{% endif %}
    </nav>
    {% endif %}
    <p class="edit"><a href="{{ repo_url }}/blob/main/README.ar.md" rel="noopener">{{ 'اقرأ الدليل المختصر على GitHub' if is_ar else 'Read README on GitHub' }}</a></p>
  </main>

  {% if page.toc and page.toc|length > 1 %}
  <aside class="toc" aria-label="{{ 'في هذه الصفحة' if is_ar else 'On this page' }}">
    <p>{{ 'في هذه الصفحة' if is_ar else 'On this page' }}</p>
    <ul>
    {% for h in page.toc %}
      <li><a href="#{{ h.id }}">{{ h.name|striptags }}</a>
      {% if h.children %}<ul>{% for c in h.children %}<li><a href="#{{ c.id }}">{{ c.name|striptags }}</a></li>{% endfor %}</ul>{% endif %}
      </li>
    {% endfor %}
    </ul>
  </aside>
  {% endif %}
</div>

<footer class="foot">
  <div class="foot-in">
    <p class="foot-brand"><strong>{{ 'جسر ديسكورد لمحطة العمل' if is_ar else 'Workstation Discord Bridge' }}</strong> — {{ 'حوار مباشر من الهاتف مع Antigravity وClaude Code' if is_ar else 'Direct mobile chat with Antigravity & Claude Code' }}</p>
    <p class="foot-meta">MIT License · <a href="{{ repo_url }}">GitHub</a> · <a href="https://warraq.benjelloun.dev">Warraq</a> · <a href="https://benjelloun.dev">benjelloun.dev</a></p>
  </div>
</footer>

<dialog class="search" id="search" aria-label="{{ 'بحث' if is_ar else 'Search' }}">
  <form method="dialog" class="search-box" role="search">
    <span>🔍</span>
    <input type="search" id="q" placeholder="{{ 'ابحث في التوثيق…' if is_ar else 'Search the docs…' }}" autocomplete="off" enterkeyhint="search">
    <button class="esc" value="close">Esc</button>
  </form>
  <ol class="results" id="results" aria-live="polite"></ol>
  <p class="no-results" id="no-results" hidden>{{ 'لا نتائج. جرّب كلمة أخرى.' if is_ar else 'No results.' }}</p>
</dialog>
<script src="/assets/js/site.js?v={{ ver }}" defer data-index="{{ '/en/search.json' if not is_ar else '/search.json' }}"></script>
</body>
</html>
"""


def asset_version() -> str:
    h = hashlib.sha1()
    for p in sorted((DOCS / "assets").rglob("*")):
        if p.is_file() and p.suffix in (".css", ".js"):
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    shutil.copytree(DOCS / "assets", OUT / "assets")
    if (DOCS / "screenshots").is_dir():
        shutil.copytree(DOCS / "screenshots", OUT / "screenshots")
    (OUT / "CNAME").write_text(f"{CNAME}\n", encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    pages: dict[str, list[DocPage]] = {"ar": [], "en": []}
    for lang in ("ar", "en"):
        lang_dir = DOCS / lang
        if not lang_dir.is_dir():
            continue
        for md_file in sorted(lang_dir.rglob("*.md")):
            rel = md_file.relative_to(lang_dir).as_posix()
            meta, body = split_front_matter(md_file.read_text(encoding="utf-8"))
            html, toc = render_md(body)
            pages[lang].append(
                DocPage(
                    lang=lang,
                    rel=rel,
                    url=url_for(lang, rel),
                    title=str(meta.get("title") or rel),
                    description=str(meta.get("description") or ""),
                    order=ORDER_MAP.get(rel, int(meta.get("order", 50))),
                    body=body,
                    html=html,
                    toc=toc,
                )
            )
        pages[lang].sort(key=lambda p: (p.order, p.rel))

    ar_top = [p for p in pages["ar"] if not p.rel.startswith("guides/") and p.rel != "faq.md"]
    ar_guides = [p for p in pages["ar"] if p.rel.startswith("guides/")]
    ar_bottom = [p for p in pages["ar"] if p.rel == "faq.md"]
    ar_seq = ar_top + ar_guides + ar_bottom

    env = Environment(autoescape=select_autoescape(["html"]))
    tmpl = env.from_string(BASE_TMPL)
    ver = asset_version()

    for lang in ("ar", "en"):
        search_items = []
        seq = ar_seq if lang == "ar" else pages["en"]
        for idx, p in enumerate(seq):
            prev_p = seq[idx - 1] if idx > 0 else None
            next_p = seq[idx + 1] if idx + 1 < len(seq) else None
            html = tmpl.render(
                lang=lang,
                page=p,
                canonical=f"{SITE_URL}{p.url}",
                ver=ver,
                repo_url=REPO_URL,
                nav_top=ar_top,
                nav_guides=ar_guides,
                nav_bottom=ar_bottom,
                prev_p=prev_p,
                next_p=next_p,
            )
            dst = OUT / p.url.lstrip("/") / "index.html"
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(html, encoding="utf-8")
            search_items.append(
                {
                    "u": p.url,
                    "t": p.title,
                    "h": "",
                    "s": "التوثيق العربي" if lang == "ar" else "English Overview",
                    "x": f"{p.description} {plain_text(p.html)}"[:2500],
                }
            )
        idx_path = OUT / ("en/search.json" if lang == "en" else "search.json")
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(json.dumps(search_items, ensure_ascii=False), encoding="utf-8")

    print(f"Built {sum(len(v) for v in pages.values())} pages into {OUT}")


if __name__ == "__main__":
    build()
