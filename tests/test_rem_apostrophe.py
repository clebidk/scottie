"""rem_to_px skipped every rem between two apostrophes in comment prose:
its string-skip matched from a "don't" in one comment to the next
apostrophe far below, across newlines. Live on listicle-test-4 on
2026-09-23: pillars body copy rendered at 11.25px (1.125rem under the
theme's 10px root)."""
from harness import css_scope


def test_an_apostrophe_in_a_comment_does_not_hide_later_rems():
    css = (
        "/* don't let the theme win */\n"
        ".a{font-size:1.125rem}\n"
        "/* the card's copy */\n"
        ".b{font-size:1rem}\n"
    )
    out = css_scope.rem_to_px(css)
    assert "rem" not in out.replace("/* don't let the theme win */", "")
    assert ".a{font-size:18px}" in out and ".b{font-size:16px}" in out


def test_a_string_does_not_run_across_a_newline():
    css = ".a::before{content:'it}\n.b{margin:2rem}"
    assert ".b{margin:32px}" in css_scope.rem_to_px(css)


def test_real_strings_and_urls_are_still_left_alone():
    css = ".a{content:\"2rem\";background:url(x-2rem.png);width:2rem}"
    assert css_scope.rem_to_px(css) == ".a{content:\"2rem\";background:url(x-2rem.png);width:32px}"
