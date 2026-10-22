import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from epubnik.builder import BookError, build
from epubnik.check import check
from epubnik.cli import main

EXAMPLE = Path(__file__).parent.parent / "examples" / "zametki"
OPF = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.fixture
def epub(tmp_path):
    return build(EXAMPLE, tmp_path / "book.epub", FIXED)


def write_book(folder: Path, toml: str, **chapters: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "book.toml").write_text(toml, encoding="utf-8")
    for name, text in chapters.items():
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    return folder


def test_example_book_builds_and_passes_the_check(epub):
    assert epub.chapters == 3
    assert epub.images == 2  # обложка и схема веток
    assert check(epub.path) == []


def test_mimetype_is_first_and_stored(epub):
    with zipfile.ZipFile(epub.path) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read(first) == b"application/epub+zip"


def test_metadata_and_spine(epub):
    with zipfile.ZipFile(epub.path) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
    assert opf.find(".//dc:title", OPF).text == "Заметки начинающего программиста"
    assert opf.find(".//dc:language", OPF).text == "ru"
    assert opf.find(".//opf:meta[@property='dcterms:modified']", OPF).text == "2026-01-02T03:04:05Z"
    assert opf.find(".//dc:identifier", OPF).text.startswith("urn:uuid:")
    spine = [i.get("idref") for i in opf.findall(".//opf:itemref", OPF)]
    assert spine == ["cover", "ch001", "ch002", "ch003"]
    cover = opf.find(".//opf:item[@properties='cover-image']", OPF)
    assert cover.get("media-type") == "image/svg+xml"


def test_toc_has_chapters_with_nested_sections(epub):
    with zipfile.ZipFile(epub.path) as z:
        nav = z.read("OEBPS/nav.xhtml").decode("utf-8")
        ncx = z.read("OEBPS/toc.ncx").decode("utf-8")
    assert '<a href="text/ch002.xhtml#git-bez-strakha">Git без страха</a><ol>' in nav
    assert "text/ch001.xhtml#pisat-kod-kazhdyy-den" in nav
    assert ncx.count("<navPoint") == epub.toc_entries == 12


def test_images_are_copied_and_links_rewritten(epub):
    with zipfile.ZipFile(epub.path) as z:
        chapter = z.read("OEBPS/text/ch002.xhtml").decode("utf-8")
        assert "OEBPS/images/branches.svg" in z.namelist()
    assert 'src="../images/branches.svg"' in chapter


def test_build_is_reproducible(tmp_path):
    a = build(EXAMPLE, tmp_path / "a.epub", FIXED).path.read_bytes()
    b = build(EXAMPLE, tmp_path / "b.epub", FIXED).path.read_bytes()
    assert hashlib.sha256(a).digest() == hashlib.sha256(b).digest()


def test_chapters_default_to_alphabetical_md_files(tmp_path):
    folder = write_book(tmp_path / "b", 'title = "T"\nauthor = "A"\n', **{"02-b": "# Два", "01-a": "# Один", "README": "x"})
    result = build(folder, tmp_path / "b.epub", FIXED)
    assert result.chapters == 2
    with zipfile.ZipFile(result.path) as z:
        assert "Один" in z.read("OEBPS/text/ch001.xhtml").decode("utf-8")


def test_chapter_without_headings_uses_the_file_name_in_the_toc(tmp_path):
    folder = write_book(tmp_path / "b", 'title = "T"\nauthor = "A"\n', vstuplenie="Просто текст.")
    result = build(folder, tmp_path / "b.epub", FIXED)
    with zipfile.ZipFile(result.path) as z:
        assert ">vstuplenie</a>" in z.read("OEBPS/nav.xhtml").decode("utf-8")


@pytest.mark.parametrize("toml, chapters, message", [
    ('author = "A"\n', {"a": "# x"}, "нужно поле title"),
    ('title = "T"\nauthor = "A"\nchapters = ["nope.md"]\n', {"a": "# x"}, "nope.md"),
    ('title = "T"\nauthor = "A"\ncover = "c.png"\n', {"a": "# x"}, "Обложка c.png не найдена"),
    ('title = "T"\nauthor = "A"\n', {"a": "![x](missing.png)"}, "картинка missing.png не найдена"),
    ('title = "T"\nauthor = "A"\ndate = "22.10.2026"\n', {"a": "# x"}, "ГГГГ-ММ-ДД"),
    ('title = "T\nauthor', {"a": "# x"}, "book.toml"),
])
def test_clear_errors_for_broken_books(tmp_path, toml, chapters, message):
    folder = write_book(tmp_path / "b", toml, **chapters)
    with pytest.raises(BookError, match=message):
        build(folder, tmp_path / "b.epub", FIXED)


def test_check_finds_broken_archives(tmp_path):
    bad = tmp_path / "bad.epub"
    with zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/container.xml", "<container/>")
        z.writestr("mimetype", "application/epub+zip")
    problems = check(bad)
    assert "mimetype должен быть первым файлом архива" in problems
    assert any("путь к OPF" in p for p in problems)
    assert check(tmp_path / "missing.epub")[0].startswith("не открывается")


def test_cli_new_build_check(tmp_path, capsys):
    folder = tmp_path / "moya-kniga"
    assert main(["new", str(folder)]) == 0
    assert main(["new", str(folder)]) == 1  # второй раз не перезаписывает
    out = tmp_path / "out.epub"
    assert main(["build", str(folder), "-o", str(out)]) == 0
    assert main(["check", str(out)]) == 0
    assert "ok" in capsys.readouterr().out
    assert main(["build", str(tmp_path / "nothing")]) == 2
