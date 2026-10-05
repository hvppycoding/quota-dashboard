"""Read private user-entered quota notes without presenting them as live inventory."""
import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def manual_claude_reset_tickets(path, now):
    try:
        file = Path(path)
        if file.stat().st_size > 16384:
            return None
        config = json.loads(file.read_text())
        ticket = config.get('claude', {}).get('resetTickets', {})
        count = ticket.get('count')
        expiry = ticket.get('expiresOn')
        if type(count) is not int or not 1 <= count <= 100 or ticket.get('type') != 'full':
            return None
        if not isinstance(expiry, str):
            return None
        date = dt.date.fromisoformat(expiry)
        if date.isoformat() != expiry:
            return None
        # User requested a conservative deadline when only the expiry date is known.
        deadline = dt.datetime.combine(date, dt.time.min, tzinfo=ZoneInfo('Asia/Seoul'))
        return {'source': 'manual', 'type': 'full', 'count': count, 'expiresOn': expiry,
                'expiresAt': deadline.astimezone(dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
                'expirationAssumption': 'start_of_day_kst', 'expirationPassed': now >= deadline}
    except (OSError, ValueError, TypeError, AttributeError):
        return None
