import email
from html.parser import HTMLParser

from email_reply_parser import EmailReplyParser


def extract_sender_email(from_header: str) -> str:
    """Extract the email address from a From header value.

    Handles both 'Name <email@example.com>' and bare 'email@example.com' formats.
    """
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip()
    return from_header.strip()


class _HTMLToText(HTMLParser):
    """Minimal HTML-to-text converter.

    Turns block-level tags into line breaks so that downstream line-based
    quote-stripping (EmailReplyParser) can see quote markers that originated
    as <blockquote>, <div>, etc.
    """

    _BLOCK_TAGS = frozenset({"br", "p", "div", "blockquote", "li", "tr"})

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def get_text(self) -> str:
        return "".join(self._chunks)


def _html_to_text(html: str) -> str:
    """Convert an HTML string to plain text, inserting newlines at block tags."""
    parser = _HTMLToText()
    parser.feed(html)
    return parser.get_text()


def extract_text_payload(msg: email.message.Message) -> str:
    """Extract the most appropriate text body from an email message.

    Prefers a text/plain part if one exists. Otherwise, falls back to the
    text/html part and converts it to plain text via _html_to_text so that
    downstream line-based quote-stripping has something to work with.
    Returns an empty string if no usable body part is found.
    """
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return _html_to_text(payload.decode("utf-8", errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            text = payload.decode("utf-8", errors="replace")
            if msg.get_content_type() == "text/html":
                return _html_to_text(text)
            return text
    return ""


def extract_email_body(msg: email.message.Message) -> str:
    """Extract just the sender's new reply, with quoted history stripped.

    Pulls the most appropriate text body out of the message and runs it
    through email-reply-parser, which removes prior-message quoting (>,
    "On ... wrote:", "-----Original Message-----", etc.). Returns an empty
    string if the sender wrote nothing new (e.g. a pure forward).
    """
    return EmailReplyParser.parse_reply(extract_text_payload(msg))
