"""Быстрая проверка структуры EPUB без внешних зависимостей.

Это не замена epubcheck: проверяется то, на чём чаще всего ломаются читалки —
контейнер, mimetype, манифест, spine, оглавление и корректность XML.
"""

from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def check(path: Path) -> list[str]:
    """Возвращает список проблем; пустой список — файл в порядке."""
    problems: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        return [f"не открывается как ZIP: {e}"]

    with z:
        infos = z.infolist()
        names = {i.filename for i in infos}
        if not infos or infos[0].filename != "mimetype":
            problems.append("mimetype должен быть первым файлом архива")
        else:
            first = infos[0]
            if first.compress_type != zipfile.ZIP_STORED:
                problems.append("mimetype должен храниться без сжатия")
            if z.read(first) != b"application/epub+zip":
                problems.append("mimetype должен содержать ровно application/epub+zip")
            if first.extra:
                problems.append("у mimetype не должно быть дополнительных полей в заголовке ZIP")

        if "META-INF/container.xml" not in names:
            return problems + ["нет META-INF/container.xml"]
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
        except ET.ParseError as e:
            return problems + [f"container.xml: некорректный XML: {e}"]
        rootfile = container.find(".//c:rootfile", NS)
        if rootfile is None or not rootfile.get("full-path"):
            return problems + ["container.xml: не указан путь к OPF"]
        opf_path = rootfile.get("full-path")
        if opf_path not in names:
            return problems + [f"OPF {opf_path} из container.xml отсутствует в архиве"]

        try:
            opf = ET.fromstring(z.read(opf_path))
        except ET.ParseError as e:
            return problems + [f"{opf_path}: некорректный XML: {e}"]
        base = posixpath.dirname(opf_path)

        for tag in ("identifier", "title", "language"):
            if opf.find(f".//dc:{tag}", NS) is None:
                problems.append(f"в метаданных нет dc:{tag}")
        if opf.find(".//opf:meta[@property='dcterms:modified']", NS) is None:
            problems.append("в метаданных нет dcterms:modified")

        items = {}
        for item in opf.findall(".//opf:manifest/opf:item", NS):
            href = posixpath.normpath(posixpath.join(base, item.get("href", "")))
            items[item.get("id")] = (href, item.get("media-type"), item.get("properties", ""))
            if href not in names:
                problems.append(f"файл из манифеста отсутствует: {href}")
        if not any("nav" in props.split() for _, _, props in items.values()):
            problems.append("в манифесте нет документа оглавления (properties=\"nav\")")

        spine = opf.findall(".//opf:spine/opf:itemref", NS)
        if not spine:
            problems.append("spine пустой: в книге нет страниц для чтения")
        for ref in spine:
            if ref.get("idref") not in items:
                problems.append(f"spine ссылается на неизвестный id {ref.get('idref')}")

        listed = {href for href, _, _ in items.values()} | {"mimetype", "META-INF/container.xml", opf_path}
        for name in sorted(names - listed):
            if not name.endswith("/") and not name.startswith("META-INF/"):
                problems.append(f"файл не указан в манифесте: {name}")

        for href, media, _ in items.values():
            if media == "application/xhtml+xml" and href in names:
                try:
                    ET.fromstring(z.read(href))
                except ET.ParseError as e:
                    problems.append(f"{href}: некорректный XHTML: {e}")
    return problems
