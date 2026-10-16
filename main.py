"""
Knowledge-base sync job.

1. Scrape support articles via the Zendesk Help Center API.
2. Normalize each article to clean Markdown (<slug>-<id>.md).
3. Upload only new/changed articles to a Gemini File Search Store (vector store).

State lives in the File Search Store itself (each document carries `article_id`
and `content_hash` metadata), so the job is stateless and can run in a fresh
Docker container every day.

Usage:
    python main.py                 # run the daily sync
    python main.py ask "question"  # ask OptiBot a question against the store
"""
import os
import re
import sys
import json
import math
import time
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Silence per-request HTTP logs from the SDK
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logger = logging.getLogger("kb-sync")

ZENDESK_DOMAIN = os.getenv("ZENDESK_DOMAIN", "https://support.optisigns.com")
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "0"))  # 0 = all articles
ARTICLES_DIR = Path(os.getenv("ARTICLES_DIR", "data/articles"))
RUN_REPORT_FILE = Path(os.getenv("RUN_REPORT_FILE", "data/last_run.json"))
STORE_DISPLAY_NAME = os.getenv("STORE_DISPLAY_NAME", "support-kb")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
UPLOAD_CONCURRENCY = int(os.getenv("UPLOAD_CONCURRENCY", "5"))
UPLOAD_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_TIMEOUT_SECONDS", "180"))

# Chunking: whitespace-token windows (Gemini File Search allows at most 512 per chunk)
MAX_TOKENS_PER_CHUNK = 512
MAX_OVERLAP_TOKENS = 128

TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504", "UNAVAILABLE", "DEADLINE", "RESOURCE_EXHAUSTED",
    "timed out", "Timeout", "Connection", "RemoteProtocolError",
)

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
\u2022 Tone: helpful, factual, concise.
\u2022 Only answer using the uploaded docs.
\u2022 Max 5 bullet points; else link to the doc.
\u2022 Cite up to 3 "Article URL:" lines per reply."""


# --------------------------------------------------------------------------- #
# Scrape & clean
# --------------------------------------------------------------------------- #
class CleanMarkdownConverter(MarkdownConverter):
    """Markdown converter that keeps fenced code blocks with their language tag."""

    # Accept both old (convert_as_inline) and new (parent_tags) markdownify signatures
    def convert_pre(self, el, text, *args, **kwargs):
        code_text = el.get_text()
        lang = ""
        code_tag = el.find("code")
        if code_tag and code_tag.get("class"):
            for cls in code_tag.get("class"):
                if cls.startswith("language-"):
                    lang = cls.replace("language-", "")
        return f"\n```{lang}\n{code_text.strip()}\n```\n"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[-\s_]+", "-", text).strip("-")


NOISE_SELECTORS = [
    "nav", "header", "footer", "script", "style", "aside", "noscript", "iframe[src*='ads']",
    ".article-votes", ".article-relatives", ".breadcrumbs", ".share", ".ad", ".advertisement",
]


def clean_html_to_markdown(html_content: str, title: str, html_url: str, base_domain: str) -> str:
    """Strip nav/ads noise, keep headings/code/links (made relative), prepend title + URL."""
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    for selector in NOISE_SELECTORS:
        for tag in soup.select(selector):
            tag.decompose()

    # Inline base64 images bloat the KB with useless tokens; keep only alt text
    for img in soup.find_all("img", src=True):
        if img["src"].startswith("data:"):
            alt = img.get("alt", "").strip()
            img.replace_with(f"[image: {alt}]" if alt else "")

    # Rewrite absolute links to the help center into relative links
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if href.startswith(base_domain):
            parsed = urlparse(href)
            rel_path = parsed.path or "/"
            if parsed.query:
                rel_path += f"?{parsed.query}"
            if parsed.fragment:
                rel_path += f"#{parsed.fragment}"
            a_tag["href"] = rel_path

    raw_md = CleanMarkdownConverter(heading_style="ATX", bullets="-", escape_misc=False).convert_soup(soup)
    # Normalize non-breaking spaces, trailing whitespace and blank-line runs
    clean_md = raw_md.replace("\u00a0", " ")
    clean_md = re.sub(r"[ \t]+\n", "\n", clean_md)
    clean_md = re.sub(r"\n{3,}", "\n\n", clean_md).strip()

    return f"# {title}\nArticle URL: {html_url}\n\n---\n\n{clean_md}\n"


def fetch_zendesk_articles(limit: int = 0) -> list:
    """Fetch published articles from Zendesk Help Center API v2 (limit 0 = all)."""
    articles = []
    url = f"{ZENDESK_DOMAIN}/api/v2/help_center/en-us/articles.json?per_page=100"

    logger.info(f"Fetching articles from {ZENDESK_DOMAIN} (limit={limit or 'all'})...")
    while url and (not limit or len(articles) < limit):
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("articles", []):
            if item.get("draft") or not item.get("body"):
                continue
            articles.append(item)
            if limit and len(articles) >= limit:
                break

        url = data.get("next_page")

    logger.info(f"Fetched {len(articles)} published articles.")
    return articles


def compute_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def estimate_chunks(text: str) -> int:
    """Chunk count for whitespace chunking with MAX_TOKENS_PER_CHUNK / MAX_OVERLAP_TOKENS."""
    tokens = len(text.split())
    if tokens <= MAX_TOKENS_PER_CHUNK:
        return 1
    step = MAX_TOKENS_PER_CHUNK - MAX_OVERLAP_TOKENS
    return 1 + math.ceil((tokens - MAX_TOKENS_PER_CHUNK) / step)


# --------------------------------------------------------------------------- #
# Delta detection
# --------------------------------------------------------------------------- #
def plan_delta(local: dict, remote: dict, full_scrape: bool) -> dict:
    """
    Compare scraped articles with documents already in the store.

    local:  {article_id: content_hash}
    remote: {article_id: [{"name": doc_name, "hash": content_hash}, ...]}
    Returns {"add": [ids], "update": [ids], "skip": [ids], "delete": [doc_names]}
    """
    plan = {"add": [], "update": [], "skip": [], "delete": []}

    for art_id, content_hash in local.items():
        docs = remote.get(art_id, [])
        if not docs:
            plan["add"].append(art_id)
            continue
        matching = [d for d in docs if d["hash"] == content_hash]
        if matching:
            plan["skip"].append(art_id)
            # Drop duplicates left behind by an interrupted run
            plan["delete"].extend(d["name"] for d in docs if d is not matching[0])
        else:
            plan["update"].append(art_id)

    # Articles removed from the help center (only safe to detect on a full scrape)
    if full_scrape:
        for art_id, docs in remote.items():
            if art_id not in local:
                plan["delete"].extend(d["name"] for d in docs)

    return plan


# --------------------------------------------------------------------------- #
# Gemini File Search Store
# --------------------------------------------------------------------------- #
def get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
    if not api_key:
        raise ValueError("Missing GEMINI_API_KEY (or API_KEY) environment variable.")
    # 60s HTTP timeout so a stuck request never hangs the job
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=60_000))


def with_retry(fn, what: str, attempts: int = 4):
    """Run fn() and retry on transient API errors with linear backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts or not any(m in str(e) for m in TRANSIENT_MARKERS):
                raise
            wait = 5 * attempt
            logger.warning(f"{what}: transient error ({str(e)[:80]}), retry {attempt} in {wait}s")
            time.sleep(wait)


def find_or_create_store(client: genai.Client) -> str:
    """Return the store name: env override, else lookup by display name, else create."""
    store_name = os.getenv("GEMINI_FILE_SEARCH_STORE")
    if store_name:
        client.file_search_stores.get(name=store_name)
        return store_name

    for store in client.file_search_stores.list():
        if store.display_name == STORE_DISPLAY_NAME:
            return store.name

    store = client.file_search_stores.create(config={"display_name": STORE_DISPLAY_NAME})
    logger.info(f"Created new File Search Store: {store.name}")
    return store.name


def list_remote_documents(client: genai.Client, store_name: str) -> dict:
    """Map article_id -> list of {name, hash} using each document's custom metadata."""
    remote = {}
    for doc in client.file_search_stores.documents.list(parent=store_name):
        meta = {m.key: m.string_value for m in (doc.custom_metadata or [])}
        art_id = meta.get("article_id")
        if not art_id or "FAILED" in str(doc.state):
            # Unknown or broken document: mark with a sentinel so it gets cleaned up
            remote.setdefault(f"orphan:{doc.name}", []).append({"name": doc.name, "hash": None})
            continue
        remote.setdefault(art_id, []).append({"name": doc.name, "hash": meta.get("content_hash")})
    return remote


def delete_document(client: genai.Client, doc_name: str):
    with_retry(
        lambda: client.file_search_stores.documents.delete(name=doc_name, config={"force": True}),
        f"delete {doc_name}",
    )


def upload_article(client: genai.Client, store_name: str, file_path: Path,
                   art_id: str, content_hash: str) -> str:
    """Upload one Markdown file with metadata and wait until it is indexed."""

    def _upload():
        return client.file_search_stores.upload_to_file_search_store(
            file_search_store_name=store_name,
            file=str(file_path),
            config={
                "display_name": file_path.name,
                "mime_type": "text/markdown",
                "custom_metadata": [
                    {"key": "article_id", "string_value": art_id},
                    {"key": "content_hash", "string_value": content_hash},
                ],
                "chunking_config": {
                    "white_space_config": {
                        "max_tokens_per_chunk": MAX_TOKENS_PER_CHUNK,
                        "max_overlap_tokens": MAX_OVERLAP_TOKENS,
                    }
                },
            },
        )

    operation = with_retry(_upload, f"upload {file_path.name}")
    if operation.error:
        raise RuntimeError(f"Upload failed: {operation.error}")
    document_name = operation.response.document_name if operation.response else None
    if not document_name:
        raise RuntimeError("Upload returned no document name")

    # operation.done is not reliably set, so poll the document state instead
    deadline = time.time() + UPLOAD_TIMEOUT_SECONDS
    while True:
        doc = with_retry(lambda: client.file_search_stores.documents.get(name=document_name), "get document")
        state = str(doc.state)
        if "ACTIVE" in state:
            return document_name
        if "FAILED" in state:
            delete_document(client, document_name)
            raise RuntimeError(f"Indexing failed (state={state})")
        if time.time() > deadline:
            raise TimeoutError(f"Indexing timed out after {UPLOAD_TIMEOUT_SECONDS}s")
        time.sleep(2)


# --------------------------------------------------------------------------- #
# Assistant
# --------------------------------------------------------------------------- #
def format_sources(response) -> str:
    """List the retrieved documents (grounding chunks) used for the answer."""
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
    except (AttributeError, IndexError, TypeError):
        return "Sources: none"
    titles = []
    for chunk in chunks:
        ctx = getattr(chunk, "retrieved_context", None)
        title = getattr(ctx, "title", None) if ctx else None
        if title and title not in titles:
            titles.append(title)
    return "Sources:\n" + "\n".join(f"- {t}" for t in titles) if titles else "Sources: none"


def ask_optibot(client: genai.Client, store_name: str, question: str) -> dict:
    """Ask OptiBot a question grounded on the File Search Store, with model fallback."""
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[types.Tool(file_search=types.FileSearch(file_search_store_names=[store_name]))],
    )
    models = [GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != GEMINI_MODEL]
    last_error = None
    for model in models:
        try:
            response = with_retry(
                lambda: client.models.generate_content(model=model, contents=question, config=config),
                model, attempts=2,
            )
            return {"model": model, "answer": response.text, "sources": format_sources(response)}
        except Exception as e:
            last_error = e
            if not any(m in str(e) for m in TRANSIENT_MARKERS):
                raise
            logger.warning(f"{model} unavailable, falling back")
    raise last_error


# --------------------------------------------------------------------------- #
# Daily sync
# --------------------------------------------------------------------------- #
def run_sync() -> dict:
    started = time.time()
    client = get_client()
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)

    store_name = find_or_create_store(client)
    logger.info(f"File Search Store: {store_name}")

    # 1. Scrape & normalize
    raw_articles = fetch_zendesk_articles(limit=MAX_ARTICLES)
    local_hashes, files, chunks_by_id = {}, {}, {}
    for art in raw_articles:
        art_id = str(art["id"])
        title = art.get("title") or "Untitled"
        md = clean_html_to_markdown(art.get("body", ""), title, art.get("html_url", ""), ZENDESK_DOMAIN)
        file_path = ARTICLES_DIR / f"{slugify(title) or 'article'}-{art_id}.md"
        file_path.write_text(md, encoding="utf-8")
        local_hashes[art_id] = compute_sha256(md)
        files[art_id] = file_path
        chunks_by_id[art_id] = estimate_chunks(md)

    # 2. Detect delta against the store
    remote = list_remote_documents(client, store_name)
    plan = plan_delta(local_hashes, remote, full_scrape=(MAX_ARTICLES == 0))
    logger.info(
        f"Delta plan: add={len(plan['add'])} update={len(plan['update'])} "
        f"skip={len(plan['skip'])} delete={len(plan['delete'])}"
    )

    # 3. Upload only the delta (upload new version first, then remove the old one)
    counts = {"added": 0, "updated": 0, "skipped": len(plan["skip"]), "deleted": 0, "failed": 0}
    files_embedded, chunks_embedded = 0, 0

    def _process(art_id: str, is_update: bool):
        upload_article(client, store_name, files[art_id], art_id, local_hashes[art_id])
        if is_update:
            for old in remote.get(art_id, []):
                delete_document(client, old["name"])
        return art_id, is_update

    jobs = [(a, False) for a in plan["add"]] + [(a, True) for a in plan["update"]]
    with ThreadPoolExecutor(max_workers=UPLOAD_CONCURRENCY) as pool:
        futures = {pool.submit(_process, a, u): a for a, u in jobs}
        for fut in as_completed(futures):
            art_id = futures[fut]
            try:
                _, is_update = fut.result()
                counts["updated" if is_update else "added"] += 1
                files_embedded += 1
                chunks_embedded += chunks_by_id[art_id]
                logger.info(f"[{'UPDATED' if is_update else 'ADDED'}] {files[art_id].name}")
            except Exception as e:
                counts["failed"] += 1
                logger.error(f"[FAILED] {files[art_id].name}: {e}")

    for doc_name in plan["delete"]:
        try:
            delete_document(client, doc_name)
            counts["deleted"] += 1
        except Exception as e:
            logger.error(f"[FAILED] delete {doc_name}: {e}")

    report = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": round(time.time() - started, 1),
        "file_search_store": store_name,
        "articles_scraped": len(raw_articles),
        **counts,
        "files_embedded": files_embedded,
        "chunks_embedded": chunks_embedded,
        "chunks_in_kb_total": sum(chunks_by_id.values()),
    }
    RUN_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUN_REPORT_FILE.write_text(json.dumps(report, indent=2), encoding="utf-8")

    logger.info("=" * 55)
    logger.info("SYNC SUMMARY")
    for key, value in report.items():
        logger.info(f"{key:<20}: {value}")
    logger.info("=" * 55)
    return report


def main(argv: list) -> int:
    if len(argv) >= 2 and argv[0] == "ask":
        client = get_client()
        result = ask_optibot(client, find_or_create_store(client), " ".join(argv[1:]))
        print(f"{result['answer']}\n\n[model: {result['model']}]\n{result['sources']}")
        return 0

    report = run_sync()
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
