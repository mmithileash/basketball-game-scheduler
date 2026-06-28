import html
import logging
import re
from datetime import date
from typing import Any

import boto3

from common.config import load_config

logger = logging.getLogger(__name__)

_config = None
_ses_client = None

_REPO_ISSUES_URL = "https://github.com/mmithileash/basketball-game-scheduler/issues"


def _report_issues_line() -> str:
    """A one-line invitation to report bugs/issues on the GitHub repo."""
    return f"Found a bug or have a suggestion? Report it at {_REPO_ISSUES_URL}"


def _get_config():
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_ses_client():
    global _ses_client
    if _ses_client is None:
        _ses_client = boto3.client("ses")
    return _ses_client


def _unsubscribe_footer() -> str:
    """Return a plain-text unsubscribe block to append to player emails."""
    config = _get_config()
    return (
        f"\n\n---\n"
        f"To unsubscribe from future game emails, click the link below "
        f"(this will open your email client with a pre-filled message):\n"
        f"mailto:{config.sender_email}?subject=UNSUBSCRIBE\n\n"
        f"{_report_issues_line()}\n"
    )


# Palette for the HTML rendering of emails. The plain-text body remains the
# single source of truth; these only colour/scale structure detected within it.
_ACCENT = "#e8702a"  # basketball orange — section headings + the 🏀 headline
_RSVP_COLORS = {
    "YES": "#1a7f37",  # green
    "NO": "#c0392b",  # red
    "MAYBE": "#b7791f",  # amber
}

# A divider is a line of box-drawing rules ("────") or three-or-more hyphens
# ("---", used in footers). A heading is a line that is *entirely* uppercase
# letters/spaces (e.g. "THE GAME", "HOW TO RESPOND"). An RSVP line leads with a
# YES/NO/MAYBE token. These are deliberately narrow so ordinary prose is never
# restyled.
_DIVIDER_LINE_RE = re.compile(r"\s*(?:─+|-{3,})\s*")
_HEADING_LINE_RE = re.compile(r"[A-Z][A-Z &'/]*")
_RSVP_LINE_RE = re.compile(r"^(\s*)(YES|NO|MAYBE)(\b.*)$")


def _style_line(line: str) -> str:
    """Style a single already-HTML-escaped body line.

    Returns an <hr> sentinel for divider lines, or the line wrapped in an accent
    span for the 🏀 headline / uppercase section headers, or the line with its
    leading RSVP token colour-coded — otherwise the line unchanged.
    """
    if _DIVIDER_LINE_RE.fullmatch(line):
        return '<hr style="border:0;border-top:1px solid #e5e7eb;margin:18px 0;">'
    if "🏀" in line:
        return (
            f'<span style="font-size:18px;font-weight:700;color:{_ACCENT};">'
            f"{line}</span>"
        )
    if _HEADING_LINE_RE.fullmatch(line.strip()):
        return (
            f'<span style="font-weight:700;color:{_ACCENT};letter-spacing:0.5px;">'
            f"{line}</span>"
        )
    m = _RSVP_LINE_RE.match(line)
    if m:
        color = _RSVP_COLORS[m.group(2)]
        return (
            f"{m.group(1)}"
            f'<span style="color:{color};font-weight:700;">{m.group(2)}</span>'
            f"{m.group(3)}"
        )
    return line


def _text_to_html(text: str) -> str:
    """Render a plain-text email body as styled HTML, preserving the body's own
    typography and making mailto: links clickable.

    The bodies are authored as plain text whose structure is carried by line
    breaks, blank lines, indentation and aligned labels. HTML normally collapses
    runs of spaces and blank lines, which would destroy that layout, so we render
    inside a `white-space: pre-wrap` card: every space and newline the author
    wrote is honoured, while long lines still soft-wrap on small screens. Because
    pre-wrap already turns "\n" into a line break, we must NOT also insert <br>.

    On top of that layout we layer light, attention-guiding styling via
    `_style_line`: box-drawing dividers become real <hr> rules (literal "─" runs
    were wider than the card and wrapped in Gmail — the original bug), the 🏀
    headline and uppercase section headers take the accent colour, and leading
    YES/NO/MAYBE tokens are colour-coded.
    """
    escaped = html.escape(text)
    # Markdown-style links `[text](url)` become anchors whose visible text is the
    # label (used e.g. to show the venue address linked to a map). Run this first
    # so the bare-URL pass below never sees the URL hiding inside the parentheses.
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    escaped = re.sub(r"(mailto:[^\s]+)", r'<a href="\1">Unsubscribe</a>', escaped)
    # Linkify bare http(s) URLs, leaving any trailing sentence punctuation outside
    # the anchor (so "see https://x.com/p." doesn't swallow the period). The
    # [^\s<"] bound and the (?<!") guard keep us from re-linking a URL already
    # sitting inside an anchor's href="..." (e.g. from the markdown pass above).
    escaped = re.sub(
        r'(?<!")(https?://[^\s<"]+?)([.,;:!?)\]]*)(?=\s|$)',
        r'<a href="\1">\1</a>\2',
        escaped,
    )

    styled = "\n".join(_style_line(line) for line in escaped.split("\n"))
    # Each <hr> is a block element carrying its own vertical margin; strip the
    # newlines hugging it so pre-wrap doesn't stack extra blank lines around it.
    styled = re.sub(r"\n*(<hr[^>]*>)\n*", r"\1", styled)

    return (
        '<html><body style="margin:0;padding:0;background-color:#f4f5f7;">'
        '<div style="max-width:600px;margin:24px auto;padding:28px;'
        "background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        'font-size:15px;line-height:1.55;color:#1a1a1a;'
        'white-space:pre-wrap;word-wrap:break-word;">'
        f"{styled}"
        "</div></body></html>"
    )


def send_email(to: str, subject: str, body: str) -> None:
    """Send an email via SES with both plain-text and HTML parts."""
    config = _get_config()
    client = _get_ses_client()

    client.send_email(
        Source=config.sender_email,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body, "Charset": "UTF-8"},
                "Html": {"Data": _text_to_html(body), "Charset": "UTF-8"},
            },
        },
    )
    logger.info(f"Sent email to {to}: {subject}")


def send_reminder(
    player_email: str,
    player_name: str | None,
    confirmed_count: int,
    game_date: str,
    min_players: int,
) -> None:
    """Send reminder email with current confirmed count and the game's minimum."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"Reminder: Basketball Game - {game_date} [Game: {game_date}]"
    body = (
        f"{greeting},\n\n"
        f"🏀  Reminder: basketball game on {_pretty_date(game_date)}.\n\n"
        f"  We currently have {confirmed_count} confirmed player(s) "
        f"(need at least {min_players}).\n\n"
        f"  Haven't responded yet?\n"
        f"  Just hit Reply with Yes / No / Maybe.\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_cancellation(player_email: str, game_date: str, min_players: int) -> None:
    """Send game cancellation notice citing the game's minimum-players figure."""
    subject = f"Cancelled: Basketball Game - {game_date} [Game: {game_date}]"
    body = (
        f"Hi,\n\n"
        f"Unfortunately, the basketball game scheduled for {game_date} "
        f"has been cancelled due to insufficient players "
        f"(fewer than {min_players} confirmed).\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_guest_followup(
    sponsor_email: str,
    sponsor_name: str | None,
    guest_names: list[str],
    game_date: str,
) -> None:
    """Ask the sponsor whether their guests are still attending after they declined."""
    greeting = f"Hi {sponsor_name}" if sponsor_name else "Hi"
    guest_list = ", ".join(guest_names)

    subject = f"Your guests for the basketball game on {game_date}"
    body = (
        f"{greeting},\n\n"
        f"We noticed you won't be able to make it to the basketball game on {game_date}. "
        f"You had listed the following guest(s): {guest_list}.\n\n"
        f"Are any of them still planning to attend?\n\n"
        f"Please reply with the names of guests who are still coming, and optionally "
        f"a contact email for each (e.g. 'John - john@example.com, Jane').\n\n"
        f"If no reply is received before Friday's cutoff, we'll assume they won't attend.\n"
    )

    send_email(sponsor_email, subject, body + _unsubscribe_footer())


def send_no_game_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
) -> None:
    """Notify a player that no game is scheduled this week (admin pre-cancelled)."""
    greeting = f"Hi {player_name}" if player_name else "Hi"

    subject = f"No Game This Week - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"There will be no basketball game this week ({game_date}). "
        f"The game has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )

    send_email(player_email, subject, body + _unsubscribe_footer())


def send_guest_cancelled_sponsor_notification(
    sponsor_email: str,
    sponsor_name: str | None,
    guest_name: str,
    game_date: str,
) -> None:
    """Notify a sponsor that their guest has cancelled their attendance."""
    greeting = f"Hi {sponsor_name}" if sponsor_name else "Hi"

    subject = f"Your guest cancelled: Basketball Game - {game_date}"
    body = (
        f"{greeting},\n\n"
        f"{guest_name} has cancelled their attendance for the basketball game on {game_date}.\n\n"
        f"If you'd like to bring another guest instead, just reply to the original announcement.\n"
    )

    send_email(sponsor_email, subject, body + _unsubscribe_footer())


def send_admin_unclear_notification(
    admin_email: str,
    player_email: str,
    raw_message: str,
    game_date: str,
) -> None:
    """Flag to the organiser that a player's message couldn't be understood.

    The system has already asked the player to clarify; this is a heads-up so a
    human can step in if the player goes silent. No reply is requested — the
    admin acts by emailing the player directly.
    """
    subject = f"Couldn't understand a reply for {game_date}"
    body = (
        f"Hi,\n\n"
        f"We couldn't confidently interpret a reply about the game on "
        f"{_pretty_date(game_date)}, so the player's roster status was left "
        f"unchanged and they were asked to clarify with Yes / No / Maybe.\n\n"
        f"  From:  {player_email}\n\n"
        f"{_DIVIDER}\n"
        f"{raw_message}\n"
        f"{_DIVIDER}\n\n"
        f"If they go quiet, you may want to follow up with them directly.\n\n"
        f"---\n"
        f"{_report_issues_line()}\n"
    )
    send_email(admin_email, subject, body)


def send_admin_weekly_prompt(admin_email: str, week_start_date: str) -> None:
    """Ask the admin whether to schedule games for the upcoming week."""
    subject = f"Schedule games for week of {week_start_date}?"
    body = (
        f"Hi,\n\n"
        f"Would you like to schedule any basketball games for the week of {week_start_date}?\n\n"
        f"Reply with the dates and optionally times "
        f"(e.g. 'Tuesday and Saturday' or 'Thursday 7PM'). "
        f"If no time is given, the default is 11:00 AM UTC.\n\n"
        f"To skip this week, reply 'No games this week'.\n\n"
        f"Please reply by Tuesday 9PM UTC.\n\n"
        f"---\n"
        f"{_report_issues_line()}\n"
    )
    send_email(admin_email, subject, body)


def send_no_game_this_week(
    player_email: str,
    player_name: str | None,
    week_start_date: str,
    reason: str,
) -> None:
    """Notify a player that no games are scheduled for the given week."""
    greeting = f"Hi {player_name}" if player_name else "Hi"
    subject = f"No Games This Week ({week_start_date})"
    if reason == "admin_declined":
        detail = "The organiser has confirmed there are no games scheduled this week."
    else:
        detail = "No games were scheduled for this week."
    body = (
        f"{greeting},\n\n"
        f"{detail}\n\n"
        f"See you next week!\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def _duration_label(hours: int) -> str:
    return f"{hours} hour{'s' if hours > 1 else ''}"


def _location_display() -> str:
    """The venue line for email bodies.

    When a map URL is configured this returns markdown `[address](url)`, which
    `_text_to_html` renders as an anchor whose visible text is the address; with
    no map URL it falls back to the bare address (unchanged from before).
    """
    config = _get_config()
    if config.game_map_url:
        return f"[{config.game_location}]({config.game_map_url})"
    return config.game_location


_DIVIDER = "─" * 44


def _pretty_date(game_date: str) -> str:
    """Render an ISO date as e.g. 'Saturday, 07 July 2026' for human readers.

    Falls back to the raw string if it isn't a parseable ISO date.
    """
    try:
        return date.fromisoformat(game_date).strftime("%A, %d %B %Y")
    except ValueError:
        return game_date


def send_tentative_announcement(
    player_email: str,
    player_name: str | None,
    game_date: str,
    policy: dict[str, Any],
) -> None:
    """Send game announcement driven by the game's policy.

    When the two tiers differ the email shows both turnout-dependent branches
    with concrete times; when they are equal it shows a single time line.
    """
    from common.policy import is_fixed

    config = _get_config()
    greeting = f"Hi {player_name}" if player_name else "Hi"
    subject = f"Basketball Game - {game_date} [Game: {game_date}]"

    long_game = policy["longGame"]
    short_game = policy["shortGame"]

    if is_fixed(policy):
        timing = (
            f"  Time:      {long_game['startTime']}\n"
            f"  Duration:  {_duration_label(int(long_game['durationHours']))}\n"
        )
    else:
        timing = (
            f"  Time:     Depends on how many of us sign up —\n"
            f"               {policy['threshold']}+ players: {long_game['startTime']} "
            f"for {_duration_label(int(long_game['durationHours']))}\n"
            f"               otherwise:   {short_game['startTime']} "
            f"for {_duration_label(int(short_game['durationHours']))}\n"
        )

    body = (
        f"{greeting},\n\n"
        f"🏀  A basketball game has been scheduled!\n\n"
        f"{_DIVIDER}\n"
        f"THE GAME\n"
        f"{_DIVIDER}\n"
        f"  Date:      {_pretty_date(game_date)}\n"
        f"{timing}"
        f"  Location:  {_location_display()}\n\n"
        f"  We need at least {policy['minPlayers']} players to play.\n\n"
        f"{_DIVIDER}\n"
        f"HOW TO RESPOND\n"
        f"{_DIVIDER}\n"
        f"  Just hit Reply to this email — we'll know which game you mean.\n\n"
        f"     YES     \"Yes\" or \"I'm in\"\n"
        f"     NO      \"No\" or \"Can't make it\"\n"
        f"     MAYBE   \"Maybe\" if you're not sure yet\n\n"
        f"  Changed your mind?\n"
        f"     Reply again any time before the cutoff — your latest answer wins.\n\n"
        f"  Bringing guests?\n"
        f"     List their names, e.g. \"I'm in, bringing John and Jane\".\n"
        f"     Add an email if you'd like them to get their own confirmation\n"
        f"     and be able to RSVP directly.\n\n"
        f"  Curious who's coming?\n"
        f"     Reply \"who's playing?\" to see the current roster.\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def send_final_confirmation_with_duration(
    player_email: str,
    game_date: str,
    roster: dict[str, Any],
    start_time: str,
    duration_hours: int,
) -> None:
    """Send final game confirmation with the locked-in start time and duration."""
    config = _get_config()
    subject = f"Confirmed: Basketball Game - {game_date} [Game: {game_date}]"

    yes_data = roster.get("YES", {})
    lines: list[str] = []
    for email, data in yes_data.get("players", {}).items():
        name = data.get("name") or email
        lines.append(f"  - {name} ({email})")
    for guest in yes_data.get("guests", []):
        lines.append(f"    + Guest: {guest['name']} (via {guest['sponsorName']})")

    roster_text = "\n".join(lines) if lines else "  (none)"

    body = (
        f"Hi,\n\n"
        f"The basketball game is ON for {game_date}!\n\n"
        f"Time: {start_time}\n"
        f"Duration: {_duration_label(duration_hours)}\n"
        f"Location: {_location_display()}\n\n"
        f"Confirmed players:\n{roster_text}\n\n"
        f"See you there!\n"
    )
    send_email(player_email, subject, body + _unsubscribe_footer())


def send_rate_limit_notice(player_email: str) -> None:
    """Send the one-time courtesy notice when a sender hits their weekly limit.

    Sent exactly once (on the crossing email); subsequent over-limit emails get
    no reply, so an auto-responder bouncing off this notice dies after one round.
    """
    config = _get_config()
    subject = "You've reached your weekly email limit"
    body = (
        f"Hi,\n\n"
        f"You've reached the limit of messages we can process from you this week, "
        f"so we won't be able to action further emails until the limit resets next Monday.\n\n"
        f"If you need something sorted out sooner, please contact the organiser "
        f"directly at {config.admin_email}.\n"
    )
    send_email(player_email, subject, body)


def send_admin_cancelled_broadcast(player_email: str, game_date: str, include_unsubscribe: bool = False) -> None:
    """Notify a player that an already-announced game has been cancelled by admin."""
    subject = f"Cancelled: Basketball Game - {game_date}"
    body = (
        f"Hi,\n\n"
        f"The basketball game scheduled for {game_date} has been cancelled by the organiser.\n\n"
        f"See you next week!\n"
    )
    if include_unsubscribe:
        body += _unsubscribe_footer()
    send_email(player_email, subject, body)
