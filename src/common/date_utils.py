from datetime import date, timedelta


def next_saturday(today: date | None = None) -> date:
    """Return the date of the coming Saturday, or today if today is Saturday."""
    if today is None:
        today = date.today()
    days_ahead = (5 - today.weekday()) % 7
    return today + timedelta(days=days_ahead)
