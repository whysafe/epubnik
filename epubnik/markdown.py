"""Небольшой конвертер Markdown в XHTML для глав книги.

Поддерживает то, что встречается в книгах: заголовки, абзацы, курсив и жирный,
код (строчный и блоками), списки (в том числе вложенные), цитаты, ссылки,
картинки, сноски [^1] и горизонтальную линию. Результат — строгий XHTML,
который принимают читалки и epubcheck.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

_INLINE_CODE = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_FOOTREF = re.compile(r"\[\^([\w-]+)\]")
_STRONG = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_EM = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_FOOTDEF = re.compile(r"^\[\^([\w-]+)\]:\s*(.*)$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


@dataclass
class Heading:
    level: int
    text: str
    anchor: str


@dataclass
class Chapter:
    """Глава после разбора: XHTML-тело, заголовки для оглавления и найденные картинки."""

    body: str
    headings: list[Heading] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.headings[0].text if self.headings else ""


_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
     "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def slugify(text: str, taken: set[str]) -> str:
    """Якорь для заголовка: только латиница и цифры (кириллица транслитерируется), уникальный в пределах главы.

    ASCII-якоря надёжнее: некоторые читалки и валидаторы плохо переносят не-ASCII во фрагментах ссылок.
    """
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    base = re.sub(r"[^a-z0-9]+", "-", latin).strip("-") or "section"
    if base[0].isdigit():
        base = "h-" + base
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


class _Inline:
    """Строчная разметка. Код и ссылки вырезаются в заглушки, чтобы внутри них не срабатывали * и _."""

    def __init__(self, images: list[str], footnotes: dict[str, int]):
        self.images = images
        self.footnotes = footnotes

    def render(self, text: str) -> str:
        stash: list[str] = []

        def keep(fragment: str) -> str:
            stash.append(fragment)
            return f"\x00{len(stash) - 1}\x00"

        text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
        text = _IMAGE.sub(lambda m: keep(self._image(m.group(2), m.group(1), m.group(3))), text)
        text = _FOOTREF.sub(lambda m: keep(self._footref(m.group(1))), text)
        text = _LINK.sub(lambda m: keep(self._link(m.group(2), m.group(1))), text)
        text = html.escape(text, quote=False)
        text = _STRONG.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
        text = _EM.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
        text = re.sub(r" {2,}$|\\$", "<br/>", text, flags=re.M)
        return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)

    def _image(self, src: str, alt: str, title: str | None) -> str:
        if not re.match(r"^[a-z]+://", src):
            self.images.append(src)
        title_attr = f' title="{html.escape(title)}"' if title else ""
        return f'<img src="{html.escape(src)}" alt="{html.escape(alt)}"{title_attr}/>'

    def _link(self, href: str, label: str) -> str:
        inner = self.render(label)
        return f'<a href="{html.escape(href)}">{inner}</a>'

    def _footref(self, key: str) -> str:
        n = self.footnotes.setdefault(key, len(self.footnotes) + 1)
        return f'<a epub:type="noteref" href="#fn-{key}" id="fnref-{key}">{n}</a>'


def convert(source: str) -> Chapter:
    """Markdown главы -> тело XHTML (без обёртки html/body)."""
    lines = source.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    chapter = Chapter(body="")
    anchors: set[str] = set()
    footnotes: dict[str, int] = {}
    definitions: dict[str, str] = {}
    inline = _Inline(chapter.images, footnotes)
    out: list[str] = []
    i = 0

    def para_end(line: str) -> bool:
        return (not line.strip() or _HEADING.match(line) is not None or line.startswith("```")
                or line.lstrip().startswith(">") or _LIST_ITEM.match(line) is not None
                or _HR.match(line) is not None)

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        if m := _FOOTDEF.match(line):
            definitions[m.group(1)] = m.group(2)
            i += 1
            continue

        if line.startswith("```"):
            lang = line[3:].strip()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # закрывающие ```
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(code))}</code></pre>")
            continue

        if m := _HEADING.match(line):
            level, text = len(m.group(1)), m.group(2)
            anchor = slugify(re.sub(r"[*_`]", "", text), anchors)
            plain = html.unescape(re.sub(r"<[^>]+>", "", inline.render(text)))
            chapter.headings.append(Heading(level, plain, anchor))
            out.append(f'<h{level} id="{anchor}">{inline.render(text)}</h{level}>')
            i += 1
            continue

        if _HR.match(line):
            out.append("<hr/>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            nested = convert("\n".join(quote))
            out.append(f"<blockquote>{nested.body}</blockquote>")
            chapter.images.extend(nested.images)
            continue

        if _LIST_ITEM.match(line):
            block: list[str] = []
            while i < len(lines) and (_LIST_ITEM.match(lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                block.append(lines[i])
                i += 1
            out.append(_render_list(block, inline))
            continue

        paragraph = [line.lstrip()]
        i += 1
        while i < len(lines) and not para_end(lines[i]) and not _FOOTDEF.match(lines[i]):
            paragraph.append(lines[i].lstrip())
            i += 1
        out.append(f"<p>{inline.render(chr(10).join(paragraph))}</p>")

    if footnotes:
        items = []
        for key, n in sorted(footnotes.items(), key=lambda kv: kv[1]):
            text = inline.render(definitions.get(key, "Текст сноски не найден."))
            items.append(f'<li><aside epub:type="footnote" id="fn-{key}"><p>{text} '
                         f'<a href="#fnref-{key}">↩</a></p></aside></li>')
        out.append('<section epub:type="footnotes"><hr/><ol class="footnotes">' + "".join(items) + "</ol></section>")

    chapter.body = "\n".join(out)
    return chapter


def _render_list(block: list[str], inline: _Inline) -> str:
    """Список с вложенностью по отступу: каждый уровень вложенности сдвинут минимум на два пробела."""
    first = _LIST_ITEM.match(block[0])
    assert first is not None
    base_indent = len(first.group(1))
    tag = "ol" if first.group(2)[0].isdigit() else "ul"
    items: list[list[str]] = []
    for line in block:
        m = _LIST_ITEM.match(line)
        if m and len(m.group(1)) <= base_indent + 1:
            items.append([m.group(3)])
        elif items:
            items[-1].append(line[base_indent + 2:] if line.startswith(" " * (base_indent + 2)) else line.strip())
    parts = []
    for item in items:
        head, rest = item[0], item[1:]
        nested = ""
        if rest and _LIST_ITEM.match(rest[0]):
            nested = _render_list(rest, inline)
        elif rest:
            head = head + " " + " ".join(r.strip() for r in rest)
        parts.append(f"<li>{inline.render(head)}{nested}</li>")
    return f"<{tag}>" + "".join(parts) + f"</{tag}>"
