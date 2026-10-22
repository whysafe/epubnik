from xml.etree import ElementTree as ET

from epubnik.markdown import convert, slugify


def well_formed(body: str) -> None:
    ET.fromstring(f'<div xmlns:epub="http://www.idpf.org/2007/ops">{body}</div>')


def test_headings_get_ascii_anchors_and_feed_the_toc():
    ch = convert("# Глава первая\n\n## Подраздел\n\n## Подраздел")
    assert [(h.level, h.text, h.anchor) for h in ch.headings] == [
        (1, "Глава первая", "glava-pervaya"),
        (2, "Подраздел", "podrazdel"),
        (2, "Подраздел", "podrazdel-2"),
    ]
    assert ch.title == "Глава первая"
    assert '<h1 id="glava-pervaya">' in ch.body


def test_slug_never_starts_with_a_digit():
    assert slugify("2026 год", set()) == "h-2026-god"


def test_inline_markup():
    body = convert("Это *курсив*, **жирный**, `код <b>` и [ссылка](https://example.com).").body
    assert "<em>курсив</em>" in body
    assert "<strong>жирный</strong>" in body
    assert "<code>код &lt;b&gt;</code>" in body
    assert '<a href="https://example.com">ссылка</a>' in body
    well_formed(body)


def test_underscores_inside_words_and_code_stay_literal():
    body = convert("Переменная snake_case_name и `__init__` не курсив.").body
    assert "<em>" not in body
    assert "snake_case_name" in body and "<code>__init__</code>" in body


def test_html_in_text_is_escaped():
    body = convert("Тег <script>alert(1)</script> & прочее").body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body and "&amp;" in body
    well_formed(body)


def test_paragraphs_join_lines_and_split_on_blank_lines():
    body = convert("строка один\nстрока два\n\nвторой абзац").body
    assert body.count("<p>") == 2
    assert "строка один\nстрока два" in body


def test_hard_line_break_with_backslash():
    assert "<br/>" in convert("первая\\\nвторая").body


def test_fenced_code_keeps_formatting_and_language():
    body = convert("```python\nif x < 1:\n    print('*не курсив*')\n```").body
    assert '<pre><code class="language-python">' in body
    assert "if x &lt; 1:\n    print(&#x27;*не курсив*&#x27;)" in body
    assert "<em>" not in body


def test_nested_lists():
    body = convert("- один\n- два\n  - два и один\n  - два и два\n- три\n\n1. первый\n2. второй").body
    assert body.count("<ul>") == 2
    assert "<li>два<ul><li>два и один</li><li>два и два</li></ul></li>" in body
    assert "<ol><li>первый</li><li>второй</li></ol>" in body
    well_formed(body)


def test_blockquote_and_horizontal_rule():
    body = convert("> цитата\n> в две строки\n\n---\n\nпосле").body
    assert body.startswith("<blockquote><p>цитата\nв две строки</p></blockquote>")
    assert "<hr/>" in body


def test_images_are_collected_except_remote_ones():
    ch = convert('![схема](img/a.png "Подпись")\n\n![логотип](https://example.com/l.png)')
    assert ch.images == ["img/a.png"]
    assert '<img src="img/a.png" alt="схема" title="Подпись"/>' in ch.body


def test_footnotes_are_numbered_by_first_use_and_listed_at_the_end():
    ch = convert("Текст[^b] и ещё[^a], снова[^b].\n\n[^a]: Сноска А.\n[^b]: Сноска Б.")
    body = ch.body
    assert body.count('epub:type="noteref"') == 3
    assert body.index('id="fn-b"') < body.index('id="fn-a"')
    assert ">1</a>" in body and ">2</a>" in body
    assert "Сноска Б." in body
    well_formed(body)


def test_missing_footnote_text_is_reported_in_place():
    assert "Текст сноски не найден." in convert("Текст[^x].").body
