# Strip Quoted History from Inbound Player Emails

**Date:** 2026-04-06
**Status:** Approved (pending user sign-off on this written spec)
**Scope:** `email_processor` Lambda; touches `src/email_processor/handler.py`, `Makefile`, and adds `requirements-runtime.txt`.

## Problem

`_extract_email_body` in [src/email_processor/handler.py:36-56](../../../src/email_processor/handler.py#L36-L56) currently returns the entire `text/plain` (or `text/html` fallback) part of an inbound reply, including any quoted history from the email thread. The body is handed straight to Bedrock for intent classification.

When a player replies to one of our bot emails, the quoted history typically contains:

- The full announcement / reminder we sent (which often contains the words "playing", "guests", "bringing", etc.)
- The previous round-trip in a multi-message thread

Bedrock can get confused by this context, classifying replies based on quoted text rather than the player's actual new content. This is preventive hardening — the user raised it as a foreseeable problem rather than reporting an observed misclassification — but the failure mode is realistic enough to fix proactively while we are touching this code.

## Goal

Strip quoted history from inbound emails before they reach Bedrock, so the model only sees what the player actually wrote in the latest reply.

## Non-goals

- No prompt-level instructions to Bedrock about quoted content (unreliable, costs tokens, doesn't actually prevent confusion).
- No sentinel marker injected into outgoing replies (the chosen library handles the common patterns without it).
- No raw-payload fallback if the parser returns an empty string (an empty string is the *correct* output when the player wrote nothing new — it should flow through to Bedrock unchanged, same as any other empty reply today).
- No changes to `announcement_sender`, `reminder_checker`, or `game_finalizer`.
- No changes to outbound email construction in `common/email_service.py`.

## Approach

Use the [`email-reply-parser`](https://pypi.org/project/email-reply-parser/) library (Zapier's Python port of GitHub's library). It is pure Python, MIT-licensed, single-file, and exposes one API:

```python
from email_reply_parser import EmailReplyParser
new_content = EmailReplyParser.parse_reply(text)
```

It handles `>` line quoting, `On <date>, <person> wrote:` lead-ins, `-----Original Message-----` headers, and signature delimiters — i.e., the patterns used by Gmail, Apple Mail, Outlook, Thunderbird, and most other clients in plain-text mode.

`email-reply-parser` operates on plain text. Inbound emails are often `multipart/alternative` (both `text/plain` and `text/html`), and the existing extractor already prefers `text/plain` when both are present. For HTML-only emails, we strip HTML tags to plain text first using stdlib `html.parser`, then run the result through `EmailReplyParser`. This serves two purposes: it lets the line-based heuristics in `EmailReplyParser` actually fire (since `<blockquote>` becomes a line break), and it gives Bedrock cleaner input regardless of quote-stripping.

Adding any third-party library to a Lambda zip requires fixing the `make package` target, which today does not install runtime dependencies — it only zips up the Python source. The reason this works is that `boto3` is provided by the Lambda Python runtime out of the box and is the only current runtime dep. Adding `email-reply-parser` forces us to install runtime requirements into the build directory before zipping. This packaging fix ships in the same PR because it's unmotivated without the new dependency.

## Detailed Design

### 1. Refactor `_extract_email_body`

Split the existing function into two helpers and a one-line orchestrator:

```python
def _extract_text_payload(msg: email.message.Message) -> str:
    """Extract the most appropriate text body from an email message,
    converting HTML to plain text if no text/plain part is available.
    """
    # (existing logic from current _extract_email_body, plus _html_to_text
    #  applied to the HTML fallback)
    ...

def _extract_email_body(msg: email.message.Message) -> str:
    """Extract just the player's new reply, with quoted history stripped."""
    return EmailReplyParser.parse_reply(_extract_text_payload(msg))
```

The public `_extract_email_body` signature does not change. All call sites continue to work.

### 2. HTML-to-text helper

New helper in [src/email_processor/handler.py](../../../src/email_processor/handler.py), stdlib only:

```python
from html.parser import HTMLParser

class _HTMLToText(HTMLParser):
    """Minimal HTML-to-text converter that turns block-level tags into
    line breaks so EmailReplyParser's line-based heuristics can fire.
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
    parser = _HTMLToText()
    parser.feed(html)
    return parser.get_text()
```

`_extract_text_payload` calls `_html_to_text` only on the HTML fallback path; plain-text payloads bypass it.

### 3. Packaging fix

Update the `package` target in [Makefile](../../../Makefile) to install runtime requirements into each function's build directory before zipping:

```make
package:
	rm -rf $(BUILD_DIR)
	@for fn in $(LAMBDA_FUNCTIONS); do \
		echo "Packaging $$fn..."; \
		mkdir -p $(BUILD_DIR)/$$fn; \
		cp -r src/common $(BUILD_DIR)/$$fn/common; \
		cp -r src/$$fn/* $(BUILD_DIR)/$$fn/; \
		pip install -r requirements-runtime.txt -t $(BUILD_DIR)/$$fn --quiet; \
		cd $(BUILD_DIR)/$$fn && zip -r ../$$fn.zip . && cd ../..; \
		rm -rf $(BUILD_DIR)/$$fn; \
		echo "Created $(BUILD_DIR)/$$fn.zip"; \
	done
```

The packaging change applies to every Lambda listed in the Makefile's `LAMBDA_FUNCTIONS` variable (currently `announcement_sender`, `email_processor`, `reminder_checker` — `game_finalizer` is not packaged via this Makefile target today and is unaffected). `announcement_sender` and `reminder_checker` get a `pip install` of `requirements-runtime.txt` that contains exactly one package (`email-reply-parser`); the install will succeed and add the package to their zip even though they don't import it. This is acceptable: ~5KB extra per zip, no runtime cost. If this becomes a concern, the followup is to introduce per-function requirements files (e.g. `email_processor/requirements.txt`); explicitly out of scope for this PR.

### 4. Requirements split

Three files, each layered on the next:

- **`requirements-runtime.txt`** (new) — what gets shipped into Lambda zips. Contents:
  ```
  email-reply-parser>=0.5.12
  ```
  Note: no `boto3`. The Lambda Python runtime provides `boto3`, so including it in the zip wastes ~10MB and risks version drift against the runtime.

- **`requirements.txt`** (modified) — for local dev outside Lambda (e.g., scripts, repl). Contents:
  ```
  -r requirements-runtime.txt
  boto3>=1.34.0
  ```

- **`requirements-dev.txt`** (unchanged) — pulls in `requirements.txt` plus pytest/moto/etc.

`make install` and CI keep using `requirements-dev.txt`. `make package` reads `requirements-runtime.txt`.

### 5. Tests

Three new unit tests in [tests/unit/test_email_processor_handler.py](../../../tests/unit/test_email_processor_handler.py), all marked `@pytest.mark.unit`:

1. **`test_extract_body_strips_on_wrote_quote`**
   - Input: a `text/plain` email body of `"I'm in!\n\nOn 2026-04-08, Scheduler wrote:\n> Are you playing this Saturday?"`
   - Assert: `_extract_email_body` returns `"I'm in!"` (whitespace-stripped equality).

2. **`test_extract_body_strips_gt_quoted_lines`**
   - Input: a `text/plain` body with `>` line-quoted prior content above the new reply.
   - Assert: extracted body contains the new reply only and none of the `>`-prefixed lines.

3. **`test_extract_body_html_fallback_strips_quotes`**
   - Input: a `multipart/alternative` email with **only** an HTML part (the test forces the HTML fallback path) containing `<div>I'm in!</div><blockquote>Are you playing this Saturday?</blockquote>`.
   - Assert: extracted body contains `"I'm in!"` and does *not* contain `"Are you playing"`.

The existing handler tests (`test_handler_join`, `test_handler_decline`, etc.) keep using simple bodies with no quoted history; they continue to pass without modification.

## Risks & Mitigations

- **`email-reply-parser` Python 3.12 compatibility.** Last released ~2020. **Mitigation:** the first implementation step is `pip install email-reply-parser` against the project's Python 3.12.13 venv and run `python -c "from email_reply_parser import EmailReplyParser; print(EmailReplyParser.parse_reply('hi\n\nOn ... wrote:\n> bye'))"`. If it doesn't install or doesn't work, fall back to vendoring the single source file from upstream into `src/common/email_reply_parser.py` (~150 lines, MIT — license retained in the vendored file).
- **Empty parser output for forwarded-only messages.** Acceptable. An empty body flows to Bedrock and gets classified as low-confidence / unknown intent, same as any other empty reply today. No special handling.
- **Library silently truncating real content.** Undetectable at runtime by definition. Fix is to swap libraries or vendor a patched version if it ever happens. Not worth pre-emptive defense.
- **Lambda zip size growth.** Negligible (~5KB for `email-reply-parser`). The packaging fix avoids adding `boto3` to the zip via the requirements split, so net zip size *decreases* for `email_processor` if `boto3` was being inadvertently included before. (It wasn't — `make package` doesn't `pip install` today — so this is neutral, not a win.)

## Open Questions

None. The three points the user explicitly approved:
1. Packaging fix in this PR — yes
2. HTML strip-then-parse — yes
3. `requirements-runtime.txt` split — yes

## Out of Scope (followups, not this PR)

- Per-function `requirements.txt` files so non-`email_processor` Lambdas don't ship `email-reply-parser`.
- Sampling actual inbound mail to measure how often HTML-only replies arrive in practice.
- Constants/enum module for `OPEN`/`CANCELLED`/`PLAYED`/`YES`/`NO`/`MAYBE` (already noted as a follow-up from a prior review).
