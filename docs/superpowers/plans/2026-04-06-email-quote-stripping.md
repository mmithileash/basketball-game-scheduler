# Email Quote-Stripping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip quoted history from inbound player emails before they reach Bedrock so the LLM only sees what the player actually wrote in the latest reply.

**Architecture:** Add the `email-reply-parser` library (PyPI, pure Python) to the email_processor Lambda. Refactor `_extract_email_body` to (a) extract a text payload — converting HTML to plain text via a small stdlib `html.parser` helper when only HTML is available — then (b) hand the text to `EmailReplyParser.parse_reply()` and return the result. Fix `make package` to install runtime dependencies into the Lambda zip, and split `requirements.txt` so `boto3` (already provided by the Lambda runtime) does not get bundled.

**Tech Stack:** Python 3.12, `email-reply-parser==0.5.12` (verified to install and parse correctly on 3.12.13), stdlib `html.parser`, pytest with `pytest-mock`. AWS Lambda packaging via `make package`.

**Spec:** [docs/superpowers/specs/2026-04-06-email-quote-stripping-design.md](../specs/2026-04-06-email-quote-stripping-design.md)

---

## File Structure

**Files this plan creates:**
- `requirements-runtime.txt` — runtime deps shipped into Lambda zips. One line: `email-reply-parser>=0.5.12`.

**Files this plan modifies:**
- `requirements.txt` — switch to `-r requirements-runtime.txt` plus local dev deps (`boto3`).
- `Makefile` — `package` target installs runtime deps into the build directory before zipping.
- `src/email_processor/handler.py` — new `_HTMLToText` class, new `_html_to_text` and `_extract_text_payload` private helpers; `_extract_email_body` becomes a one-line orchestrator that runs the text payload through `EmailReplyParser.parse_reply()`.
- `tests/unit/test_email_processor_handler.py` — three new unit tests (one per quote-stripping scenario).

**Files this plan does NOT touch:**
- Other Lambdas (`announcement_sender`, `reminder_checker`, `game_finalizer`) — packaging change applies to them, but no code changes.
- `src/common/email_service.py` — outbound construction unchanged.
- Existing handler tests stay valid because their bodies do not contain quoted history.

---

## Pre-flight: branch state

This plan executes on the `feat/strip-quoted-email-history` branch (already created off `main`, with the design spec already committed as `309a4e5`).

If executing in a fresh environment, confirm with:
```bash
git branch --show-current  # should print: feat/strip-quoted-email-history
git log --oneline -1       # should print: 309a4e5 docs(specs): add design ...
```

---

## Task 1: Split requirements files

**Why first:** Subsequent tasks need a place to declare `email-reply-parser`. Splitting `requirements.txt` is a self-contained, reversible change.

**Files:**
- Create: `requirements-runtime.txt`
- Modify: `requirements.txt`

- [ ] **Step 1: Create `requirements-runtime.txt`**

Create the file with exactly this content:

```
email-reply-parser>=0.5.12
```

(One line, trailing newline. No `boto3` — Lambda runtime provides it.)

- [ ] **Step 2: Rewrite `requirements.txt`**

Replace the current contents of `requirements.txt`:

```
boto3>=1.34.0
```

with:

```
-r requirements-runtime.txt
boto3>=1.34.0
```

- [ ] **Step 3: Verify both files install cleanly**

Run:
```bash
.venv/bin/pip install -r requirements.txt
```

Expected: pip installs `email-reply-parser-0.5.12` (and confirms `boto3` is already satisfied). No errors.

- [ ] **Step 4: Verify `requirements-dev.txt` still resolves**

Run:
```bash
.venv/bin/pip install -r requirements-dev.txt
```

Expected: success. `requirements-dev.txt` already does `-r requirements.txt`, so the new transitive `email-reply-parser` is picked up automatically. No edit needed to `requirements-dev.txt`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-runtime.txt
git commit -m "build: split requirements into runtime and dev tiers

requirements-runtime.txt now lists only what ships into the Lambda zip
(currently just email-reply-parser, the new quote-stripping dependency).
requirements.txt -r's it and adds boto3 for local dev — boto3 is already
provided by the Lambda Python runtime so it doesn't belong in the zip."
```

---

## Task 2: Update `make package` to install runtime deps into the zip

**Why before code changes:** Once Task 3 imports `email_reply_parser`, the Lambda will fail at runtime unless the package is in the zip. Doing this now means every later commit on this branch produces a deployable artifact.

**Files:**
- Modify: `Makefile` (the `package` target)

- [ ] **Step 1: Read the current `package` target**

Run:
```bash
sed -n '/^package:/,/^[a-z]/p' Makefile
```

Confirm it currently looks like:
```make
package:
	rm -rf $(BUILD_DIR)
	@for fn in $(LAMBDA_FUNCTIONS); do \
		echo "Packaging $$fn..."; \
		mkdir -p $(BUILD_DIR)/$$fn; \
		cp -r src/common $(BUILD_DIR)/$$fn/common; \
		cp -r src/$$fn/* $(BUILD_DIR)/$$fn/; \
		cd $(BUILD_DIR)/$$fn && zip -r ../$$fn.zip . && cd ../..; \
		rm -rf $(BUILD_DIR)/$$fn; \
		echo "Created $(BUILD_DIR)/$$fn.zip"; \
	done
```

- [ ] **Step 2: Replace the `package` target**

Use the Edit tool to replace the entire `package:` block above with this exact text (note the new `pip install` line between `cp -r src/$$fn/*` and `cd $(BUILD_DIR)/$$fn && zip ...`):

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

- [ ] **Step 3: Run `make package` and verify each zip contains `email_reply_parser`**

Run:
```bash
make package
```

Expected: three lines like `Created build/announcement_sender.zip`, `Created build/email_processor.zip`, `Created build/reminder_checker.zip`. No pip errors.

Then verify the new dep landed in each zip:
```bash
for fn in announcement_sender email_processor reminder_checker; do
  echo "=== $fn ==="
  unzip -l build/$fn.zip | grep -c email_reply_parser
done
```

Expected: each line prints a number `>= 1` (the package contributes a few files).

- [ ] **Step 4: Verify `boto3` is NOT in the zip**

```bash
unzip -l build/email_processor.zip | grep -c "boto3/" || echo "0"
```

Expected: `0`. (`requirements-runtime.txt` does not list `boto3`, so it must not be bundled.)

- [ ] **Step 5: Clean up build artifacts**

```bash
make clean
```

- [ ] **Step 6: Commit**

```bash
git add Makefile
git commit -m "build(package): install runtime deps into the Lambda zip

make package previously zipped only Python source. boto3 worked because
it ships with the Lambda Python runtime, but any new third-party
dependency would have been missing at invocation time. Install
requirements-runtime.txt into each function's build dir before zipping."
```

---

## Task 3: Add `_html_to_text` helper

**Why this is its own task:** The HTML-to-text helper is a self-contained, pure function with no dependency on `email-reply-parser`. Landing it first lets us test it in isolation, and Task 4 can depend on it without ambiguity.

**Files:**
- Modify: `src/email_processor/handler.py` (add new helper near the top, after the existing imports)
- Modify: `tests/unit/test_email_processor_handler.py` (add unit test for the helper)

- [ ] **Step 1: Write the failing test**

Add this test at the end of `tests/unit/test_email_processor_handler.py`:

```python
@pytest.mark.unit
def test_html_to_text_inserts_newlines_at_block_tags():
    """_html_to_text turns block-level tags into line breaks so that
    EmailReplyParser's line-based heuristics can later see quote markers
    that originated as <blockquote> elements.
    """
    from email_processor.handler import _html_to_text

    html = (
        '<div>I\'m in!</div>'
        '<div class="gmail_quote">'
        '<div>On Mon, Apr 8, 2026, Scheduler &lt;scheduler@example.com&gt; wrote:</div>'
        '<blockquote>Are you playing this Saturday?</blockquote>'
        '</div>'
    )
    text = _html_to_text(html)

    # Block tags become newlines, so the "On ... wrote:" line and the
    # blockquote content end up on separate lines from the user's reply.
    assert "I'm in!" in text
    assert "On Mon, Apr 8, 2026, Scheduler <scheduler@example.com> wrote:" in text
    assert "Are you playing this Saturday?" in text
    # Each of those three pieces of text should appear on its own line.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert "I'm in!" in lines
    assert "Are you playing this Saturday?" in lines
```

- [ ] **Step 2: Run the test, expect ImportError**

```bash
pytest tests/unit/test_email_processor_handler.py::test_html_to_text_inserts_newlines_at_block_tags -v
```

Expected: FAIL with `ImportError: cannot import name '_html_to_text' from 'email_processor.handler'`.

- [ ] **Step 3: Add the helper to `src/email_processor/handler.py`**

Find the existing import block at the top of the file. Add `from html.parser import HTMLParser` to the stdlib imports (alphabetically after `from email import policy`).

Then, after the existing `_get_s3_client` function and before `_extract_email_body`, insert the new helper class and function:

```python
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
```

- [ ] **Step 4: Run the test, expect PASS**

```bash
pytest tests/unit/test_email_processor_handler.py::test_html_to_text_inserts_newlines_at_block_tags -v
```

Expected: PASS.

- [ ] **Step 5: Run the full handler test file to confirm no regressions**

```bash
pytest tests/unit/test_email_processor_handler.py -v
```

Expected: all existing tests still pass (this branch is off `main`, so the test count is whatever exists on `main` plus the one new test).

- [ ] **Step 6: Commit**

```bash
git add src/email_processor/handler.py tests/unit/test_email_processor_handler.py
git commit -m "feat(email-processor): add _html_to_text helper

Stdlib html.parser-based HTML-to-text converter that inserts newlines at
block-level tags. Used by the upcoming quote-stripper to give line-based
heuristics something to bite on when an inbound email is HTML-only."
```

---

## Task 4: Refactor `_extract_email_body` to use `EmailReplyParser`

This is the meat of the change. We split the current `_extract_email_body` into a lower-level `_extract_text_payload` (which gains the HTML-to-text step) and a one-line `_extract_email_body` that runs the result through the parser. We add three tests up front using TDD: one for `On ... wrote:` quoting, one for `>` line quoting, and one for HTML `<blockquote>` quoting.

**Files:**
- Modify: `src/email_processor/handler.py:36-56` (rename + new orchestrator + import the parser)
- Modify: `tests/unit/test_email_processor_handler.py` (three new tests)

- [ ] **Step 1: Write the three failing tests**

Add these three tests at the end of `tests/unit/test_email_processor_handler.py`:

```python
@pytest.mark.unit
def test_extract_body_strips_on_wrote_quote():
    """A plain-text reply with an 'On ... wrote:' quoted block keeps only
    the new content above the quote line.
    """
    from email_processor.handler import _extract_email_body

    body = (
        "I'm in!\n"
        "\n"
        "On Mon, Apr 8, 2026 at 9:00 AM, Scheduler <scheduler@example.com> wrote:\n"
        "> Are you playing this Saturday?\n"
        "> Reply YES to confirm."
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = _extract_email_body(parsed)
    assert extracted.strip() == "I'm in!"
    assert "Scheduler" not in extracted
    assert "Reply YES" not in extracted


@pytest.mark.unit
def test_extract_body_strips_gt_quoted_lines():
    """A plain-text reply where the prior message is line-quoted with '>'
    keeps only the user's new content.
    """
    from email_processor.handler import _extract_email_body

    body = (
        "Sure I'll bring 2 friends\n"
        "\n"
        "> On 2026-04-08, Scheduler wrote:\n"
        "> Reminder: game on Saturday at 10 AM\n"
        "> Bring water"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = _extract_email_body(parsed)
    assert "Sure I'll bring 2 friends" in extracted
    assert "Reminder" not in extracted
    assert "Bring water" not in extracted


@pytest.mark.unit
def test_extract_body_html_fallback_strips_quotes():
    """An HTML-only reply: the HTML is converted to text first, then
    quoted history is stripped. The user's new content survives.
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText as MIMETextHelper

    from email_processor.handler import _extract_email_body

    html = (
        '<div>I\'m in!</div>'
        '<div class="gmail_quote">'
        '<div>On Mon, Apr 8, 2026, Scheduler &lt;scheduler@example.com&gt; wrote:</div>'
        '<blockquote>Are you playing this Saturday?</blockquote>'
        '</div>'
    )

    # Build a multipart/alternative message that has ONLY an HTML part,
    # forcing _extract_text_payload to take the HTML fallback path.
    msg = MIMEMultipart("alternative")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Re: Basketball Game"
    msg.attach(MIMETextHelper(html, "html", "utf-8"))

    import email as email_lib
    from email import policy
    parsed = email_lib.message_from_bytes(msg.as_bytes(), policy=policy.default)

    extracted = _extract_email_body(parsed)
    assert "I'm in!" in extracted
    assert "Are you playing" not in extracted
    assert "Scheduler" not in extracted
```

- [ ] **Step 2: Run the three tests, expect failures**

```bash
pytest tests/unit/test_email_processor_handler.py::test_extract_body_strips_on_wrote_quote tests/unit/test_email_processor_handler.py::test_extract_body_strips_gt_quoted_lines tests/unit/test_email_processor_handler.py::test_extract_body_html_fallback_strips_quotes -v
```

Expected: all three FAIL — `test_extract_body_strips_on_wrote_quote` and `test_extract_body_strips_gt_quoted_lines` because the current `_extract_email_body` returns the entire payload including the quoted history; `test_extract_body_html_fallback_strips_quotes` because the current code returns the raw HTML string (containing `Are you playing`).

- [ ] **Step 3: Add the import for `EmailReplyParser`**

In `src/email_processor/handler.py`, add this import to the third-party import block (after `import boto3`):

```python
from email_reply_parser import EmailReplyParser
```

- [ ] **Step 4: Refactor `_extract_email_body` into `_extract_text_payload` + `_extract_email_body`**

Find the existing `_extract_email_body` function in `src/email_processor/handler.py` (currently lines 36-56 on `main`):

```python
def _extract_email_body(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
        # Fallback: try HTML if no plain text found
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""
```

Replace it with these two functions:

```python
def _extract_text_payload(msg: email.message.Message) -> str:
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


def _extract_email_body(msg: email.message.Message) -> str:
    """Extract just the player's new reply, with quoted history stripped.

    Pulls the most appropriate text body out of the message and runs it
    through email-reply-parser, which removes prior-message quoting (>,
    "On ... wrote:", "-----Original Message-----", etc.). Returns an empty
    string if the player wrote nothing new (e.g. a pure forward) — that
    flows through to Bedrock the same as any other empty reply.
    """
    return EmailReplyParser.parse_reply(_extract_text_payload(msg))
```

- [ ] **Step 5: Run the three new tests, expect PASS**

```bash
pytest tests/unit/test_email_processor_handler.py::test_extract_body_strips_on_wrote_quote tests/unit/test_email_processor_handler.py::test_extract_body_strips_gt_quoted_lines tests/unit/test_email_processor_handler.py::test_extract_body_html_fallback_strips_quotes -v
```

Expected: all three PASS.

- [ ] **Step 6: Run the full unit test suite to confirm no regressions**

```bash
make test-unit
```

Expected: every test passes. The existing `test_handler_email_parsing` test still works because its body (`"I'm playing this week!"`) has no quoted history — `EmailReplyParser.parse_reply` returns it unchanged. The existing `test_handler_join`, `test_handler_decline`, `test_handler_query_roster`, etc. all use bodies like `"I'm in!"` / `"Can't make it"` / `"Who's playing?"` which similarly have no quoted history and pass straight through.

If any test fails, STOP. The most likely cause is `_extract_email_body` returning something unexpected for a body with no quoted history — verify by adding a temporary `print` and re-running. Do not "fix" failing tests by changing assertions; the parser should be a no-op for clean bodies, and if it isn't, that's a real bug to investigate before continuing.

- [ ] **Step 7: Commit**

```bash
git add src/email_processor/handler.py tests/unit/test_email_processor_handler.py
git commit -m "feat(email-processor): strip quoted history from inbound emails

Inbound replies often contain the full quoted text of our previous bot
email. Bedrock can be confused by that quoted context (e.g. classifying
a 'Thanks!' reply as BRING_GUESTS because the quoted reminder mentioned
guests). Run extracted bodies through email-reply-parser, which strips
'On ... wrote:' lead-ins, '>' line quoting, and similar patterns.

For HTML-only emails, convert HTML to text via _html_to_text first so
the parser's line-based heuristics can fire on what was originally
<blockquote> markup.

Empty parser output (pure forwards with no new content) flows through
unchanged — Bedrock handles empty bodies the same as any other low-
confidence input."
```

---

## Task 5: Confirm the packaging change still works after code changes

**Why:** Task 2 ran `make package` against code that didn't yet `import email_reply_parser`. Now the code does. Re-verify that the packaged zip is importable end-to-end.

**Files:** none modified.

- [ ] **Step 1: Build the package**

```bash
make package
```

Expected: success, three zips created.

- [ ] **Step 2: Verify the email_processor zip imports cleanly**

Unzip into a temporary directory and import the handler with `email_reply_parser` resolved from the zip's bundled location:

```bash
mkdir -p /tmp/lambda-zip-check
unzip -o build/email_processor.zip -d /tmp/lambda-zip-check > /dev/null
cd /tmp/lambda-zip-check && python -c "import handler; print('OK:', handler._extract_email_body.__doc__[:60])"
cd - > /dev/null
rm -rf /tmp/lambda-zip-check
```

Expected: prints `OK: Extract just the player's new reply, with quoted history str...` (or similar — confirms both that `handler` is importable from the zip's flat layout and that `email_reply_parser` resolved from the bundled site-packages).

- [ ] **Step 3: Clean up build artifacts**

```bash
make clean
```

- [ ] **Step 4: No commit**

(This task is verification only; nothing changed.)

---

## Task 6: Final verification and PR

- [ ] **Step 1: Run the full unit suite one more time**

```bash
make test-unit
```

Expected: all tests pass, including the four new ones from Tasks 3 and 4 (`test_html_to_text_inserts_newlines_at_block_tags`, `test_extract_body_strips_on_wrote_quote`, `test_extract_body_strips_gt_quoted_lines`, `test_extract_body_html_fallback_strips_quotes`).

- [ ] **Step 2: Review the branch's commits**

```bash
git log --oneline main..HEAD
```

Expected (in order, oldest first):
```
309a4e5 docs(specs): add design for stripping quoted history from inbound emails
<sha>   build: split requirements into runtime and dev tiers
<sha>   build(package): install runtime deps into the Lambda zip
<sha>   feat(email-processor): add _html_to_text helper
<sha>   feat(email-processor): strip quoted history from inbound emails
```

Five commits total. If the count is off, do not squash blindly — investigate which task missed its commit step.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/strip-quoted-email-history
```

- [ ] **Step 4: Open a PR**

```bash
gh pr create --title "feat(email-processor): strip quoted history from inbound emails" --body "$(cat <<'EOF'
## Summary
- Inbound player replies often contain the full quoted text of our previous bot email. Bedrock can be confused by quoted context — e.g. classifying a "Thanks!" reply as `BRING_GUESTS` because the quoted reminder mentioned guests.
- Adds `email-reply-parser` (PyPI, pure Python, MIT) to the email_processor Lambda. `_extract_email_body` now runs the extracted text through `EmailReplyParser.parse_reply()`, which strips `>` quoting, `On ... wrote:` lead-ins, and similar patterns.
- For HTML-only emails, the body is first converted to plain text via a small stdlib `html.parser`-based helper (`_html_to_text`) so the parser's line-based heuristics can fire on what was originally `<blockquote>` markup.
- Fixes `make package` to actually install runtime dependencies into the Lambda zip (it previously only zipped Python source — `boto3` worked because the Lambda runtime provides it). Splits `requirements.txt` into a runtime tier (shipped) and a dev tier (kept out of the zip) so `boto3` doesn't get bundled.

## Design doc
[docs/superpowers/specs/2026-04-06-email-quote-stripping-design.md](docs/superpowers/specs/2026-04-06-email-quote-stripping-design.md)

## Test plan
- [x] `make test-unit` — all tests pass, including 4 new ones:
  - `test_html_to_text_inserts_newlines_at_block_tags`
  - `test_extract_body_strips_on_wrote_quote`
  - `test_extract_body_strips_gt_quoted_lines`
  - `test_extract_body_html_fallback_strips_quotes`
- [x] `make package` succeeds; `email_reply_parser` is present in each Lambda zip; `boto3/` is not.
- [x] Manual round-trip: unzip `build/email_processor.zip` into a temp dir, `python -c "import handler"` succeeds (confirms the bundled `email_reply_parser` resolves at import time).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Done.**

Return the PR URL printed by `gh pr create`.

---

## Spec Coverage Check

- **Spec §1 (Library choice — `email-reply-parser`)** → Task 1 (declares the dep), Task 2 (packages it), Task 4 (uses it).
- **Spec §2 (Plug-in point in `_extract_email_body`)** → Task 4, steps 3-4.
- **Spec §3 (HTML-to-text helper)** → Task 3 (implements + tests in isolation), Task 4 step 4 (wires it into the HTML fallback path).
- **Spec §4 (Packaging fix)** → Task 2.
- **Spec §5 (Requirements split)** → Task 1.
- **Spec §6 (Three new tests)** → Task 4 step 1 covers all three.
- **Spec §7 (Python 3.12 compatibility risk)** → Resolved before plan-write: `email-reply-parser==0.5.12` was confirmed to install and parse correctly on the project's Python 3.12.13 venv. No vendor fallback needed; plan executes deterministically.
- **Spec §"Non-goals"** → Plan does not touch outbound email construction, prompt-level Bedrock instructions, sentinel markers, raw-payload fallback, or other Lambdas. Confirmed.

## Notes for the executing engineer

- **Why the test for `_html_to_text` (Task 3) is separate from the `_extract_email_body` tests (Task 4):** The HTML-to-text helper is a pure function with no other dependencies and benefits from being tested in isolation. If something later regresses, a failing `_html_to_text` test points at the helper, while a failing `_extract_email_body` test points at the orchestration.
- **Why `_extract_email_body` is one line:** Single responsibility — it composes two smaller pieces. If you ever need to change *what* gets stripped, you change `EmailReplyParser` (or wrap it). If you ever need to change *what* gets extracted, you change `_extract_text_payload`. Don't be tempted to inline either back.
- **Why the `text/html` non-multipart case handles HTML too:** Some clients send a single-part `text/html` message with no `text/plain` alternative. The non-multipart branch in `_extract_text_payload` checks `msg.get_content_type()` and runs HTML through `_html_to_text` for consistency. The original code would have returned raw HTML in this case; the new code does the right thing without growing the test surface.
- **Empty parser output is OK and is not a bug.** If you see `EmailReplyParser.parse_reply` return `""` for an input that "looks like it had content," that means the input was entirely a quoted reply (e.g. a pure forward). The handler will hand `""` to Bedrock, which will classify it as low-confidence — same as today's behavior for any blank reply. Do NOT add a fallback to the raw payload; that's explicitly excluded by the spec.
