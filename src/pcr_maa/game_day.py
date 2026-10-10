"""CN game date at 05:00 Beijing time; never proof of server refresh."""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def current_game_day(now=None):
    """Return ISO game date. Naive input explicitly means Beijing wall time."""
    if now is None:
        now = datetime.now(BEIJING)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING)
    else:
        now = now.astimezone(BEIJING)
    return (now - timedelta(hours=5)).date().isoformat()
