"""Командная строка: epubnik build | check | new."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .builder import BookError, build
from .check import check

TEMPLATE_TOML = """\
title = "{title}"
author = "Имя Автора"
language = "ru"
description = "О чём эта книга"
# date = "2026-01-01"         # дата издания, она же фиксирует время сборки
# cover = "cover.png"         # обложка: png, jpg, gif, svg или webp
# chapters = ["01-start.md"]  # порядок глав; без него берутся все *.md по алфавиту
"""

TEMPLATE_CHAPTER = """\
# Глава первая

Текст абзаца. Можно писать *курсивом*, **жирным** и `кодом`, ставить [ссылки](https://example.com)
и сноски[^1].

## Подраздел

- пункт списка
- ещё один
  - вложенный

> Цитата тоже работает.

[^1]: Текст сноски окажется в конце главы.
"""


def _human(size: int) -> str:
    return f"{size / 1024:.1f} КБ" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} МБ"


def cmd_build(args: argparse.Namespace) -> int:
    modified = None
    if epoch := os.environ.get("SOURCE_DATE_EPOCH"):
        modified = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    result = build(Path(args.folder), Path(args.output) if args.output else None, modified)
    print(f"{result.path}: глав {result.chapters}, картинок {result.images}, "
          f"пунктов оглавления {result.toc_entries}, {_human(result.size)}")
    problems = check(result.path)
    for p in problems:
        print(f"  внимание: {p}", file=sys.stderr)
    return 1 if problems else 0


def cmd_check(args: argparse.Namespace) -> int:
    status = 0
    for name in args.files:
        problems = check(Path(name))
        if problems:
            status = 1
            print(f"{name}: найдено проблем {len(problems)}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"{name}: ok")
    return status


def cmd_new(args: argparse.Namespace) -> int:
    folder = Path(args.folder)
    if (folder / "book.toml").exists():
        print(f"{folder / 'book.toml'} уже существует", file=sys.stderr)
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "book.toml").write_text(TEMPLATE_TOML.format(title=folder.name), encoding="utf-8")
    (folder / "01-start.md").write_text(TEMPLATE_CHAPTER, encoding="utf-8")
    print(f"Шаблон книги создан в {folder}. Соберите: epubnik build {folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="epubnik", description="Markdown-главы в книгу EPUB 3")
    parser.add_argument("--version", action="version", version=f"epubnik {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="собрать книгу из папки с book.toml")
    p.add_argument("folder")
    p.add_argument("-o", "--output", help="файл .epub (по умолчанию рядом с папкой)")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("check", help="проверить структуру готового .epub")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("new", help="создать шаблон книги")
    p.add_argument("folder")
    p.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BookError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2
