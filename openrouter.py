"""Opt-in CodexBar adapter. Public responses contain only validated USD scalars."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import pwd
import re
import signal
import subprocess
from activity import read_activity

CLI = '/Applications/CodexBar.app/Contents/Helpers/CodexBarCLI'
USD_FIELDS = (
    'balanceUSD', 'accountTotalUsageUSD', 'accountTotalAddedUSD',
    'keyLimitUSD', 'keyRemainingUSD', 'keyLifetimeUsageUSD',
    'keyDailyUsageUSD', 'keyWeeklyUsageUSD', 'keyMonthlyUsageUSD',
)
DETAIL_FIELDS = {
    ('Credits', 'Remaining'): 'balanceUSD',
    ('Credits', 'Used'): 'accountTotalUsageUSD',
    ('Credits', 'Total added'): 'accountTotalAddedUSD',
    ('API key', 'API key limit'): 'keyLimitUSD',
    ('API key', 'API key remaining'): 'keyRemainingUSD',
    ('API key', 'API key used'): 'keyLifetimeUsageUSD',
    ('API key', 'Today'): 'keyDailyUsageUSD',
    ('API key', 'This week'): 'keyWeeklyUsageUSD',
    ('API key', 'This month'): 'keyMonthlyUsageUSD',
}


def dollar_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and 0 <= value <= 1e12 else None


def dollar_string(value):
    # CodexBar's plugin renders dollars, not cents. Never relay arbitrary text.
    if not isinstance(value, str) or not re.fullmatch(r'\$\d{1,13}\.\d{2}', value):
        return None
    return dollar_number(float(value[1:]))


def public_metrics(metrics):
    """Project again at the HTTP boundary; unknown keys/text cannot escape."""
    out = {field: dollar_number(metrics.get(field)) for field in USD_FIELDS}
    limit = out['keyLimitUSD']
    status = metrics.get('keyLimitStatus')
    out['keyLimitStatus'] = (
        'configured' if limit is not None and limit > 0
        else 'unlimited' if status == 'unlimited' else 'unavailable'
    )
    percent = dollar_number(metrics.get('keyLimitUsedPercent'))
    out['keyLimitUsedPercent'] = (
        percent if out['keyLimitStatus'] == 'configured' and percent is not None and percent <= 100
        else None
    )
    # Ordinary CodexBar usage JSON does not expose verified 30-day account history.
    out['account30DayStatus'] = 'not_queried_management_metric'
    return out


def project_openrouter(rows, observed_at=None):
    if not isinstance(rows, list):
        raise ValueError('openrouter_usage_unavailable')
    candidates = [row for row in rows if isinstance(row, dict) and row.get('provider') == 'openrouter']
    if len(candidates) != 1 or candidates[0].get('error') or candidates[0].get('source') != 'api':
        raise ValueError('openrouter_usage_unavailable')
    usage = candidates[0].get('usage')
    if not isinstance(usage, dict):
        raise ValueError('openrouter_usage_unavailable')
    metrics = {}
    details = usage.get('details')
    for section in details if isinstance(details, list) else []:
        if not isinstance(section, dict) or not isinstance(section.get('rows'), list):
            continue
        title = section.get('title')
        if title not in ('Credits', 'API key'):
            continue
        for row in section['rows']:
            if not isinstance(row, dict) or not isinstance(row.get('label'), str):
                continue
            field = DETAIL_FIELDS.get((title, row['label']))
            if field:
                parsed = dollar_string(row.get('value'))
                if parsed is not None:
                    metrics[field] = parsed
            if title == 'API key' and row['label'] == 'API key limit' and row.get('value') == 'No limit configured':
                metrics['keyLimitStatus'] = 'unlimited'
    cost = usage.get('providerCost')
    if isinstance(cost, dict) and cost.get('currencyCode') == 'USD':
        value = dollar_number(cost.get('used'))
        period = cost.get('period')
        field = {
            'This month (API key)': 'keyMonthlyUsageUSD',
            'Total key usage': 'keyLifetimeUsageUSD',
            'Total account usage': 'accountTotalUsageUSD',
        }.get(period) if isinstance(period,str) else None
        if field and value is not None:
            metrics[field] = value
        balance = dollar_number(cost.get('balance'))
        if balance is not None:
            metrics['balanceUSD'] = balance
    primary = usage.get('primary')
    if isinstance(primary, dict) and primary.get('isSyntheticPlaceholder') is not True:
        metrics['keyLimitUsedPercent'] = primary.get('usedPercent')
    metrics = public_metrics(metrics)
    if not any(metrics[field] is not None for field in USD_FIELDS) and metrics['keyLimitStatus'] != 'unlimited':
        raise ValueError('openrouter_usage_unavailable')
    observed_at = observed_at or dt.datetime.now(dt.timezone.utc)
    return {'observedAt': observed_at.isoformat(timespec='seconds').replace('+00:00', 'Z'), 'metrics': metrics}


def read_openrouter(*, enabled=False):
    # Gate before touching the credential environment or launching CodexBar.
    if not enabled:
        raise ValueError('openrouter_connection_pending')
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key or not key.strip():
        raise ValueError('openrouter_key_missing')
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    owner = pwd.getpwuid(os.getuid()).pw_name
    env = {
        'HOME': str(home), 'USER': owner, 'LOGNAME': owner,
        'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
        'LANG': 'en_US.UTF-8', 'CODEXBAR_CONFIG': str(Path(__file__).with_name('config.cli.json')),
        'OPENROUTER_API_KEY': key,
    }
    # No shell, credential file, endpoint override, management key, or raw logs.
    process = subprocess.Popen(
        [CLI, 'usage', '--provider', 'openrouter', '--source', 'api', '--format', 'json', '--json-only'],
        env=env, cwd=str(Path(__file__).resolve().parent), stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        raw, _ = process.communicate(timeout=45)
        if process.returncode != 0 or len(raw) > 1048576:
            raise ValueError('openrouter_usage_unavailable')
        try:
            rows = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError('openrouter_usage_unavailable') from None
        observation = project_openrouter(rows)
        observation['activity'] = read_activity()
        return observation
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
