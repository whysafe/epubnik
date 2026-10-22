"""Сборка EPUB 3 из папки с Markdown-главами и файлом book.toml."""

from __future__ import annotations

import html
import posixpath
import re
import tomllib
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .markdown import Chapter, convert

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

STYLE = """\
body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1, h2, h3 { font-family: sans-serif; line-height: 1.2; page-break-after: avoid; }
h1 { font-size: 1.6em; margin: 1.5em 0 1em; }
p { margin: 0; text-indent: 1.5em; text-align: justify; }
h1 + p, h2 + p, h3 + p, blockquote p, li p { text-indent: 0; }
blockquote { margin: 1em 1.5em; font-style: italic; }
pre { white-space: pre-wrap; font-size: .85em; background: #f4f4f4; padding: .6em; }
code { font-family: monospace; }
img { max-width: 100%; display: block; margin: 1em auto; }
hr { border: 0; border-top: 1px solid #999; margin: 1.5em 30%; }
ol.footnotes { font-size: .85em; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-height: 100%; }
"""


class BookError(Exception):
    """Ошибка в исходниках книги, понятная автору: нет файла, неверный book.toml и т. п."""


@dataclass
class Book:
    title: str
    author: str
    language: str = "ru"
    description: str = ""
    publisher: str = ""
    date: str = ""
    cover: Path | None = None
    chapters: list[Path] = field(default_factory=list)
    identifier: str = ""

    @classmethod
    def load(cls, folder: Path) -> "Book":
        folder = Path(folder)
        meta_path = folder / "book.toml"
        if not meta_path.is_file():
            raise BookError(f"Нет файла {meta_path}. Создайте шаблон: epubnik new {folder}")
        try:
            meta = tomllib.loads(meta_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise BookError(f"book.toml: {e}") from e
        for key in ("title", "author"):
            if not str(meta.get(key, "")).strip():
                raise BookError(f"book.toml: нужно поле {key}")

        names = meta.get("chapters") or sorted(p.name for p in folder.glob("*.md") if p.name.lower() != "readme.md")
        chapters = []
        for name in names:
            path = folder / name
            if not path.is_file():
                raise BookError(f"Глава {name} из book.toml не найдена")
            chapters.append(path)
        if not chapters:
            raise BookError(f"В {folder} нет глав (*.md)")

        cover = folder / meta["cover"] if meta.get("cover") else None
        if cover is not None and not cover.is_file():
            raise BookError(f"Обложка {meta['cover']} не найдена")
        if cover is not None and cover.suffix.lower() not in MEDIA_TYPES:
            raise BookError(f"Обложка должна быть картинкой: {', '.join(MEDIA_TYPES)}")

        date = str(meta.get("date", ""))
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise BookError("book.toml: date в формате ГГГГ-ММ-ДД")

        title, author = str(meta["title"]).strip(), str(meta["author"]).strip()
        identifier = str(meta.get("identifier") or f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'epubnik:{title}:{author}')}")
        return cls(title=title, author=author, language=str(meta.get("language", "ru")),
                   description=str(meta.get("description", "")), publisher=str(meta.get("publisher", "")),
                   date=date, cover=cover, chapters=chapters, identifier=identifier)


@dataclass
class BuildResult:
    path: Path
    chapters: int
    images: int
    toc_entries: int
    size: int


def _x(text: str) -> str:
    return html.escape(text, quote=True)


def _xhtml(title: str, lang: str, body: str, css: str = "../style.css", body_class: str = "") -> str:
    cls = f' class="{body_class}"' if body_class else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'lang="{_x(lang)}" xml:lang="{_x(lang)}">\n'
        f'<head><meta charset="utf-8"/><title>{_x(title)}</title>'
        f'<link rel="stylesheet" type="text/css" href="{css}"/></head>\n'
        f"<body{cls}>\n{body}\n</body>\n</html>\n"
    )


def _safe_name(path: str, taken: set[str]) -> str:
    stem, ext = posixpath.splitext(posixpath.basename(path))
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "image"
    name, n = f"{stem}{ext.lower()}", 2
    while name in taken:
        name, n = f"{stem}-{n}{ext.lower()}", n + 1
    taken.add(name)
    return name


def build(folder: Path, output: Path | None = None, modified: datetime | None = None) -> BuildResult:
    """Собирает книгу. modified фиксирует dcterms:modified и время в архиве (для воспроизводимой сборки)."""
    folder = Path(folder)
    book = Book.load(folder)
    output = Path(output) if output else folder.with_suffix(".epub")
    if modified is None:
        modified = (datetime.fromisoformat(book.date).replace(tzinfo=timezone.utc) if book.date
                    else datetime.now(timezone.utc))
    modified = modified.astimezone(timezone.utc).replace(microsecond=0)

    files: dict[str, bytes] = {}
    manifest: list[tuple[str, str, str, str]] = []  # id, href, media-type, properties
    spine: list[str] = []
    image_names: set[str] = set()
    image_map: dict[Path, str] = {}
    toc: list[tuple[str, list]] = []  # (xhtml-ссылка, заголовок, дети)

    def add_image(src: Path, origin: str) -> str:
        src = src.resolve()
        if src in image_map:
            return image_map[src]
        if not src.is_file():
            raise BookError(f"{origin}: картинка {src.name} не найдена ({src})")
        media = MEDIA_TYPES.get(src.suffix.lower())
        if not media:
            raise BookError(f"{origin}: формат {src.suffix} не поддерживается, нужен один из {', '.join(MEDIA_TYPES)}")
        name = _safe_name(src.name, image_names)
        href = f"images/{name}"
        files[f"OEBPS/{href}"] = src.read_bytes()
        manifest.append((f"img-{len(image_map) + 1}", href, media, ""))
        image_map[src] = href
        return href

    if book.cover:
        cover_href = add_image(book.cover, "book.toml")
        manifest[-1] = (manifest[-1][0], cover_href, manifest[-1][2], "cover-image")
        files["OEBPS/text/cover.xhtml"] = _xhtml(
            book.title, book.language,
            f'<section epub:type="cover"><img src="../{cover_href}" alt="{_x(book.title)}"/></section>',
            body_class="cover").encode("utf-8")
        manifest.append(("cover", "text/cover.xhtml", "application/xhtml+xml", ""))
        spine.append("cover")

    for n, path in enumerate(book.chapters, start=1):
        chapter: Chapter = convert(path.read_text(encoding="utf-8"))
        body = chapter.body
        for src in dict.fromkeys(chapter.images):
            href = add_image(path.parent / src, path.name)
            body = body.replace(f'src="{_x(src)}"', f'src="../{href}"')
        name = f"ch{n:03d}.xhtml"
        title = chapter.title or path.stem
        files[f"OEBPS/text/{name}"] = _xhtml(title, book.language, body).encode("utf-8")
        manifest.append((f"ch{n:03d}", f"text/{name}", "application/xhtml+xml", ""))
        spine.append(f"ch{n:03d}")

        # Оглавление: заголовки первого уровня главы, вложенные — второго. Без заголовков — имя файла.
        top = min((h.level for h in chapter.headings), default=1)
        entries: list = []
        for h in chapter.headings:
            link = (f"text/{name}#{h.anchor}", h.text, [])
            if h.level == top or not entries:
                entries.append(link)
            elif h.level == top + 1:
                entries[-1][2].append(link)
        toc.extend(entries or [(f"text/{name}", title, [])])

    files["OEBPS/style.css"] = STYLE.encode("utf-8")
    manifest.append(("css", "style.css", "text/css", ""))
    files["OEBPS/nav.xhtml"] = _nav(book, toc).encode("utf-8")
    manifest.append(("nav", "nav.xhtml", "application/xhtml+xml", "nav"))
    files["OEBPS/toc.ncx"] = _ncx(book, toc).encode("utf-8")
    manifest.append(("ncx", "toc.ncx", "application/x-dtbncx+xml", ""))
    files["OEBPS/content.opf"] = _opf(book, manifest, spine, modified).encode("utf-8")
    files["META-INF/container.xml"] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>\n'
        "</container>\n").encode("utf-8")

    _write_zip(output, files, modified)
    count = sum(1 + len(children) for _, _, children in toc)
    return BuildResult(output, len(book.chapters), len(image_map), count, output.stat().st_size)


def _write_zip(output: Path, files: dict[str, bytes], modified: datetime) -> None:
    """mimetype первым и без сжатия, как требует OCF; время файлов одинаковое, поэтому архив воспроизводим."""
    stamp = (max(modified.year, 1980), modified.month, modified.day, modified.hour, modified.minute, modified.second)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as z:
        info = zipfile.ZipInfo("mimetype", date_time=stamp)
        info.compress_type = zipfile.ZIP_STORED
        z.writestr(info, b"application/epub+zip")
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])


def _opf(book: Book, manifest: list, spine: list[str], modified: datetime) -> str:
    meta = [
        f'<dc:identifier id="bookid">{_x(book.identifier)}</dc:identifier>',
        f"<dc:title>{_x(book.title)}</dc:title>",
        f'<dc:creator id="author">{_x(book.author)}</dc:creator>',
        f"<dc:language>{_x(book.language)}</dc:language>",
        f'<meta property="dcterms:modified">{modified.strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>',
    ]
    if book.description:
        meta.append(f"<dc:description>{_x(book.description)}</dc:description>")
    if book.publisher:
        meta.append(f"<dc:publisher>{_x(book.publisher)}</dc:publisher>")
    if book.date:
        meta.append(f"<dc:date>{book.date}</dc:date>")
    cover_id = next((m[0] for m in manifest if m[3] == "cover-image"), None)
    if cover_id:
        meta.append(f'<meta name="cover" content="{cover_id}"/>')  # для читалок EPUB 2
    items = "\n    ".join(
        f'<item id="{i}" href="{_x(href)}" media-type="{media}"' + (f' properties="{props}"' if props else "") + "/>"
        for i, href, media, props in manifest)
    refs = "\n    ".join(f'<itemref idref="{i}"/>' for i in spine)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{_x(book.language)}">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n    ' + "\n    ".join(meta) + "\n  </metadata>\n"
        f"  <manifest>\n    {items}\n  </manifest>\n"
        f'  <spine toc="ncx">\n    {refs}\n  </spine>\n'
        "</package>\n")


def _nav(book: Book, toc: list) -> str:
    def ol(entries: list) -> str:
        return "<ol>" + "".join(
            f'<li><a href="{_x(href)}">{_x(text)}</a>{ol(children) if children else ""}</li>'
            for href, text, children in entries) + "</ol>"

    first = toc[0][0].split("#")[0] if toc else "text/ch001.xhtml"
    body = (f'<nav epub:type="toc" id="toc"><h1>Оглавление</h1>{ol(toc)}</nav>\n'
            f'<nav epub:type="landmarks" hidden=""><ol><li><a epub:type="bodymatter" href="{first}">Начало</a></li></ol></nav>')
    return _xhtml(book.title, book.language, body, css="style.css")


def _ncx(book: Book, toc: list) -> str:
    counter = [0]

    def points(entries: list) -> str:
        out = []
        for href, text, children in entries:
            counter[0] += 1
            out.append(f'<navPoint id="np{counter[0]}" playOrder="{counter[0]}"><navLabel><text>{_x(text)}</text></navLabel>'
                       f'<content src="{_x(href)}"/>{points(children)}</navPoint>')
        return "".join(out)

    nav_map = points(toc)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        f'  <head><meta name="dtb:uid" content="{_x(book.identifier)}"/></head>\n'
        f"  <docTitle><text>{_x(book.title)}</text></docTitle>\n"
        f"  <navMap>{nav_map}</navMap>\n</ncx>\n")
