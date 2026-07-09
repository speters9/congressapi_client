import logging
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "br", "li", "ul", "ol", "div", "tr"}


class _HTMLTextExtractor(HTMLParser):
    """Extracts plain text from HTML, converting block-level tags into newlines."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        self._chunks.append(data)

    def get_text(self):
        return "".join(self._chunks)


def strip_html_tags(text):
    """
    Strip HTML tags from a string (e.g. CRS bill summary text) and unescape HTML entities.

    Uses the standard library's html.parser to properly parse the markup (rather than
    regex), converting common block-level tags (<p>, <br>, <li>, etc.) into newlines so
    paragraph/list structure is preserved as plain text.

    Args:
        text (str | None): Raw HTML text.

    Returns:
        str | None: Cleaned plain text, or None if input was None.
    """
    if text is None:
        return None

    parser = _HTMLTextExtractor()
    parser.feed(text)
    parser.close()
    cleaned = parser.get_text()

    # Collapse extra blank lines/whitespace left behind by removed tags
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)

    return cleaned.strip()


def logger_setup(logger_name="Congress Client", log_level=logging.INFO, propagate=False):
    """
    Set up and return a logger with the specified name and level.
    Avoids affecting the root logger by setting propagate to False.

    Args:
        logger_name (str): The name of the logger.
        log_level (int): The logging level (e.g., logging.INFO, logging.DEBUG).

    Returns:
        logger (logging.Logger): Configured logger instance.
    """
    # Retrieve or create a logger
    logger = logging.getLogger(logger_name)

    # Avoid adding duplicate handlers if already set up
    if not logger.hasHandlers():
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)  # Match handler level to logger level

        # Set the format for the handler
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s - raised_by: %(name)s',
            datefmt='%Y-%m-%d %H:%M:%S'
            )
        console_handler.setFormatter(formatter)

        # Add the handler to the logger
        logger.addHandler(console_handler)

    # Set the logger level explicitly and prevent it from propagating to the root
    logger.setLevel(log_level)
    logger.propagate = propagate

    return logger
