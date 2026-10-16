from main import clean_html_to_markdown, slugify, compute_sha256

BASE = "https://support.optisigns.com"


def _convert(html: str) -> str:
    return clean_html_to_markdown(html, title="YouTube Guide", html_url=f"{BASE}/hc/en-us/articles/999", base_domain=BASE)


def test_slugify():
    assert slugify("How to add a YouTube Video?") == "how-to-add-a-youtube-video"
    assert slugify("  Weird__Title -- (v2)!  ") == "weird-title-v2"


def test_clean_html_preserves_relative_links_and_code():
    raw_html = """
    <nav>Top Navigation Menu</nav>
    <div class="article-body">
        <h2>Step 1: Setup</h2>
        <p>Read the <a href="https://support.optisigns.com/hc/en-us/articles/12345">Quick Guide</a>.</p>
        <pre><code class="language-bash">docker run -e API_KEY=123 main.py</code></pre>
    </div>
    <footer>Footer Ads</footer>
    """
    md = _convert(raw_html)
    assert md.startswith("# YouTube Guide\nArticle URL: https://support.optisigns.com/hc/en-us/articles/999")
    assert "Top Navigation Menu" not in md
    assert "Footer Ads" not in md
    assert "## Step 1: Setup" in md
    assert "[Quick Guide](/hc/en-us/articles/12345)" in md
    assert "```bash\ndocker run -e API_KEY=123 main.py\n```" in md


def test_external_links_are_kept_absolute():
    md = _convert('<p>See <a href="https://www.youtube.com/watch?v=abc">video</a></p>')
    assert "[video](https://www.youtube.com/watch?v=abc)" in md


def test_ads_and_scripts_removed_and_whitespace_normalized():
    md = _convert('<div class="advertisement">BUY NOW</div><script>alert(1)</script><p>A\u00a0B</p><p></p><p></p><p>C</p>')
    assert "BUY NOW" not in md
    assert "alert" not in md
    assert "A B" in md
    assert "\n\n\n" not in md


def test_inline_base64_images_removed_but_regular_images_kept():
    md = _convert('<p><img src="data:image/png;base64,AAAA" alt="diagram"><img src="/hc/img/a.png" alt="shot"></p>')
    assert "base64" not in md
    assert "[image: diagram]" in md
    assert "![shot](/hc/img/a.png)" in md


def test_empty_body_returns_empty_string():
    assert _convert("") == ""


def test_compute_sha256_deterministic():
    assert compute_sha256("sample markdown") == compute_sha256("sample markdown")
    assert compute_sha256("a") != compute_sha256("b")
