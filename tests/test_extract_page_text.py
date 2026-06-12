"""Tests for tool_service.extract_page_text — main-content extraction.

Pins the 2026-06-12 fix: nav-heavy pages must not eat the token budget
(trafilatura main-content path), and pathological HTML must still return
something (BeautifulSoup fallback).
"""
import tool_service.main as ts


def _article_page(nav_links: int = 200) -> str:
    nav = "".join(
        f'<li><a href="/section/{i}">Menu item {i}</a></li>' for i in range(nav_links)
    )
    return f"""
    <html><head><title>Recipe page</title></head><body>
      <nav><ul>{nav}</ul></nav>
      <article>
        <h1>Nashville Hot Chicken</h1>
        <p>{'A long paragraph about frying chicken with cayenne and buttermilk. ' * 30}</p>
        <h2>Ingredients</h2>
        <ul><li>1 cup buttermilk</li><li>2 tbsp cayenne pepper</li></ul>
        <h2>Directions</h2>
        <p>Marinate the chicken overnight, then fry until crisp.</p>
      </article>
      <footer>{nav}</footer>
    </body></html>
    """


def test_main_content_survives_nav_heavy_page():
    text = ts.extract_page_text(_article_page())
    assert "buttermilk" in text
    assert "cayenne" in text
    assert "Directions" in text or "Marinate" in text
    # The nav farm must not dominate the extract.
    assert text.count("Menu item") < 20


def test_token_budget_truncation():
    huge = "<html><body><article><p>" + ("word " * 50000) + "</p></article></body></html>"
    text = ts.extract_page_text(huge, token_limit=100)
    assert len(text) <= 100 * 4 + len("\n\n[truncated]")
    assert text.endswith("[truncated]")


def test_fallback_on_pathological_html():
    # No <article>/<main>, no paragraphs — trafilatura may return nothing;
    # the BS4 fallback must still produce the visible text.
    junk = "<html><body><div><span>only fragment text here</span></div></body></html>"
    text = ts.extract_page_text(junk)
    assert "only fragment text here" in text


def test_empty_input_returns_empty():
    assert ts.extract_page_text("") == ""
