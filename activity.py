"""Read-only OpenRouter activity; expose only daily model totals."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.request


def finite(value, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('invalid_activity')
    if not math.isfinite(value) or not 0 <= value <= 1e15 or integer and int(value) != value:
        raise ValueError('invalid_activity')
    return int(value) if integer else value


def project_activity(rows, now):
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError('invalid_activity')
    # The official endpoint reports completed UTC days, never today's partial day.
    dates = [(now.astimezone(dt.timezone.utc).date() - dt.timedelta(days=i)).isoformat()
             for i in range(7, 0, -1)]
    totals = {}
    daily = {date: {} for date in dates}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_activity')
        raw_date = row.get('date')
        if not isinstance(raw_date, str) or len(raw_date) > 40:
            raise ValueError('invalid_activity_date')
        try:
            stamp = dt.datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('invalid_activity_date') from None
        # Activity reports UTC day boundaries, including timezone-free SQL timestamps.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=dt.timezone.utc)
        date = stamp.astimezone(dt.timezone.utc).date().isoformat()
        if date not in daily:
            continue
        model = row.get('model')
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError('invalid_activity')
        tokens = finite(row.get('prompt_tokens'), True) + finite(row.get('completion_tokens'), True)
        requests = finite(row.get('requests'), True)
        spend = finite(row.get('usage'))
        for group in (totals, daily[date]):
            target = group.setdefault(model, {'tokens': 0, 'requests': 0, 'spendUSD': 0})
            target['tokens'] += tokens
            target['requests'] += requests
            target['spendUSD'] += spend
    ranked = sorted(totals, key=lambda model: (-totals[model]['tokens'], model))
    top = ranked[:5]
    groups = [(model, [model]) for model in top]
    if len(ranked) > 5:
        groups.append(('Others', ranked[5:]))
    def summed(source, members):
        return {field: sum(source.get(model, {}).get(field, 0) for model in members)
                for field in ('tokens', 'requests', 'spendUSD')}
    return {'status': 'available', 'observedAt': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'timezone': 'UTC', 'dates': dates,
            'spendUSD': sum(value['spendUSD'] for value in totals.values()),
            'models': [{'id': str(index), 'name': name, **summed(totals, members)}
                       for index, (name, members) in enumerate(groups)],
            'days': [{'date': date, 'tokens': [summed(daily[date], members)['tokens']
                                             for _, members in groups]} for date in dates]}


def activity_key(primary_key=None, config_path=None):
    # Follow CodexBar's configured management-key precedence. Never persist keys.
    if config_path is not None:
        try:
            config = json.loads(Path(config_path).read_text())
            for provider in config.get('providers', []):
                if provider.get('id') == 'openrouter':
                    key = (provider.get('pluginSecrets') or {}).get('OPENROUTER_MANAGEMENT_API_KEY')
                    if isinstance(key, str) and key.strip():
                        return key.strip()
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    for name in ('OPENROUTER_MANAGEMENT_API_KEY', 'OPENROUTER_MANAGEMENT_KEY'):
        key = os.environ.get(name)
        if key and key.strip():
            return key.strip()
    # CodexBar also accepts a management key in the primary API-key field.
    key = primary_key or os.environ.get('OPENROUTER_API_KEY')
    return key.strip() if isinstance(key, str) and key.strip() else None


def read_activity(primary_key=None, config_path=None):
    key = activity_key(primary_key, config_path)
    if not key:
        return {'status': 'credential_missing'}
    request = urllib.request.Request('https://openrouter.ai/api/v1/activity',
                                     headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError('activity_response_limit')
        return project_activity(json.loads(raw).get('data'), dt.datetime.now(dt.timezone.utc))
    except urllib.error.HTTPError as error:
        status = {401: 'authentication_failed', 403: 'permission_denied'}.get(error.code, 'unavailable')
        return {'status': status}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'status': 'unavailable'}
