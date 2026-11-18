"""Reader-mode HTML to markdown extraction for the model facing web tools.

An LLM pays for every character it reads, so ``fetch_webpage`` never forwards a
raw page: navigation, menus, cookie banners, scripts and footers are removed
first and the remaining article is turned into markdown.

The extractor is a chain, in this order:

* ``trafilatura`` when it is installed (best boilerplate removal);
* a stdlib reader-mode fallback using ``html.parser`` so the tool keeps
  working on a bare installation and in the test suite.

``trafilatura`` output is validated against the page chrome that was found in
the raw document: when a navigation or footer token survives extraction the
result is discarded and the deterministic fallback is used instead.

Made with ❤️ by codx-junior
"""

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Text below this length is not worth spending tokens on ("thin" page).
MIN_TEXT_CHARS = 200

#: A page whose readable text is a tiny fraction of its markup is thin too
#: (JavaScript shells, cookie walls, redirect stubs).
MIN_TEXT_RATIO = 0.05

#: Phrases that only appear on anti-bot interstitials. A page matching one of
#: them must be escalated to the browser instead of being read as content.
BOT_WALL_MARKERS = (
    "just a moment",
    "enable javascript and cookies to continue",
    "checking your browser",
    "verify you are human",
    "attention required",
    "cf-browser-verification",
    "access denied",
    "captcha",
)

#: Blocks that never carry article content.
CHROME_BLOCK_RE = re.compile(
    r"<(nav|footer|aside|header)\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS_RE = re.compile(r"[*_`~]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACES_RE = re.compile(r"[ \t]+")
_WORD_RE = re.compile(r"[A-Za-z0-9]{6,}")


@dataclass
class ExtractedContent:
    """Result of a reader-mode extraction.

    Attributes:
        extractor: Which strategy produced ``markdown`` (``trafilatura``,
            ``reader`` or ``empty``).
        markdown: The cleaned article, chrome removed.
        url: Final URL the HTML was read from.
        title: Document title, when the page declares one.
        html_len: Length of the source HTML, used by :func:`is_thin`.
    """

    extractor: str
    markdown: str = ""
    url: str = ""
    title: str = ""
    html_len: int = 0

    @property
    def text(self) -> str:
        """The readable text without markdown syntax."""
        return text_from_markdown(self.markdown)

    @property
    def text_len(self) -> int:
        """Number of readable characters."""
        return len(self.text)

    @property
    def is_empty(self) -> bool:
        """True when nothing readable was extracted."""
        return not self.markdown.strip()


class _ReaderModeParser(HTMLParser):  # pragma: no cover - exercised via tests
    """Minimal reader-mode renderer: keep prose, drop chrome, keep links."""

    SKIP_TAGS = frozenset({
        "head", "script", "style", "noscript", "nav", "footer", "aside",
        "form", "svg", "iframe", "template", "button", "select", "option",
    })
    HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    BLOCK_TAGS = frozenset({
        "p", "div", "section", "article", "main", "br", "tr", "blockquote",
        "pre", "ul", "ol", "table", "figure", "figcaption", "dl", "dt", "dd",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0
        self._link_href: Optional[str] = None
        self._link_text: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            self._link_href = dict(attrs).get("href") or ""
            self._link_text = []
            return
        if tag in self.HEADING_TAGS:
            self.parts.append("\n\n" + "#" * self.HEADING_TAGS[tag] + " ")
            return
        if tag == "li":
            self.parts.append("\n- ")
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a":
            text = " ".join("".join(self._link_text).split())
            href = (self._link_href or "").strip()
            if text and href.startswith(("http://", "https://", "/")):
                self.parts.append(f"[{text}]({href})")
            elif text:
                self.parts.append(text)
            self._link_href = None
            self._link_text = []
            return
        if tag in self.HEADING_TAGS or tag in self.BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._link_href is not None:
            self._link_text.append(data)
            return
        self.parts.append(data)


def text_from_markdown(markdown: str) -> str:
    """Return the readable text of a markdown fragment.

    Images are dropped, links keep their label, emphasis markers are removed
    and whitespace is collapsed to single spaces.
    """
    text = _IMAGE_RE.sub("", markdown or "")
    text = _LINK_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    text = text.replace("\n", " ")
    return _SPACES_RE.sub(" ", text).strip()


def clean_markdown(markdown: str) -> str:
    """Normalise newlines and collapse three or more blank lines into one."""
    text = (markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate_markdown(markdown: str, limit: Optional[int]) -> tuple:
    """Cut ``markdown`` to ``limit`` characters.

    Args:
        markdown: Text to cut.
        limit: Maximum length; ``None`` or ``<= 0`` means no limit.

    Returns:
        tuple: ``(text, truncated)``.
    """
    text = markdown or ""
    if limit is None or limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


def is_thin(content: ExtractedContent, html_len: Optional[int] = None) -> bool:
    """Whether an extraction is too poor to hand to a model.

    Args:
        content: Extraction to test.
        html_len: Length of the source HTML; defaults to the one recorded on
            ``content``. The ratio check only applies when it is available.

    Returns:
        bool: True when the page is (or behaves like) a JavaScript shell.
    """
    text_len = content.text_len
    if text_len < MIN_TEXT_CHARS:
        return True
    source_len = content.html_len if html_len is None else html_len
    if source_len and text_len / source_len < MIN_TEXT_RATIO:
        return True
    return False


def bot_wall_reason(html: str) -> Optional[str]:
    """Detect an anti-bot interstitial.

    Args:
        html: Raw page body.

    Returns:
        Optional[str]: The matched marker, or ``None`` when the page looks
        like real content.
    """
    lowered = (html or "").lower()
    for marker in BOT_WALL_MARKERS:
        if marker in lowered:
            return marker
    return None


def _trafilatura_markdown(html: str, url: str = "") -> str:
    """Extract with trafilatura, or return ``""`` when it is unusable."""
    try:
        import trafilatura
    except ImportError:
        return ""
    try:
        extracted = trafilatura.extract(
            html,
            url=url or None,
            output_format="markdown",
            include_links=True,
            include_comments=False,
            include_tables=True,
        )
    except Exception as ex:  # pragma: no cover - depends on the installed version
        logger.debug("trafilatura extraction failed: %s", ex)
        return ""
    return clean_markdown(extracted or "")


def _reader_markdown(html: str) -> str:
    """Extract with the stdlib reader-mode fallback."""
    parser = _ReaderModeParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as ex:  # pragma: no cover - defensive
        logger.debug("reader mode parser failed: %s", ex)
    return clean_markdown("".join(parser.parts))


def _chrome_tokens(html: str) -> set:
    """Words found inside navigation / footer / aside / header blocks."""
    tokens = set()
    for match in CHROME_BLOCK_RE.finditer(html or ""):
        tokens.update(_WORD_RE.findall(match.group(2)))
    return tokens


def _leaks_chrome(markdown: str, tokens: set) -> bool:
    """True when a chrome token survived the extraction."""
    if not tokens:
        return False
    lowered = (markdown or "").lower()
    return any(token.lower() in lowered for token in tokens)


def extract_content(html: str, url: str = "") -> ExtractedContent:
    """Turn a raw HTML document into clean markdown.

    Args:
        html: Raw page body.
        url: Final URL, used by extractors that resolve relative links.

    Returns:
        ExtractedContent: The article plus the strategy that produced it.

    Example:
        >>> extract_content("<html><body><p>hi</p></body></html>").extractor
        'reader'
    """
    raw = html or ""
    if not raw.strip():
        return ExtractedContent(extractor="empty", markdown="", url=url, html_len=0)

    title_match = _TITLE_RE.search(raw)
    title = " ".join(title_match.group(1).split()) if title_match else ""

    markdown = ""
    extractor = "empty"
    tokens = _chrome_tokens(raw)
    candidate = _trafilatura_markdown(raw, url)
    if candidate and not _leaks_chrome(candidate, tokens):
        markdown, extractor = candidate, "trafilatura"
    else:
        candidate = _reader_markdown(raw)
        if candidate:
            markdown, extractor = candidate, "reader"

    return ExtractedContent(
        extractor=extractor,
        markdown=markdown,
        url=url,
        title=title,
        html_len=len(raw),
    )
