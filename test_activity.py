import datetime as dt
import unittest
from activity import project_activity, read_activity, activity_key
from unittest.mock import patch
import json
import tempfile
from pathlib import Path
import urllib.error
import io


class ActivityTests(unittest.TestCase):
    now = dt.datetime(2026, 10, 5, 3, tzinfo=dt.timezone.utc)

    def row(self, name, tokens, date='2026-10-04', requests=2, spend=0.1):
        return dict(model=name, prompt_tokens=tokens, completion_tokens=3,
                    date=date, requests=requests, usage=spend,
                    cached_tokens=50, reasoning_tokens=80, endpoint_id='private-id')

    def test_top_five_and_others_preserve_totals_and_daily_series(self):
        rows = [self.row('model-'+str(i), i*100) for i in range(8)]
        rows += [self.row('model-7', 15, '2026-09-28'), self.row('excluded-today', 99999, '2026-10-05')]
        data = project_activity(rows, self.now)
        self.assertEqual([m['name'] for m in data['models']],
                         ['model-7', 'model-6', 'model-5', 'model-4', 'model-3', 'Others'])
        self.assertEqual(sum(m['tokens'] for m in data['models']), 2800+8*3+18)
        self.assertEqual(sum(sum(day['tokens']) for day in data['days']), 2842)
        self.assertEqual(sum(m['requests'] for m in data['models']), 18)
        self.assertAlmostEqual(data['spendUSD'], .9)
        self.assertEqual(data['dates'], ['2026-09-28','2026-09-29','2026-09-30','2026-10-01','2026-10-02','2026-10-03','2026-10-04'])
        self.assertNotIn('private-id', str(data))

    def test_zero_is_only_zero_when_successfully_observed(self):
        data = project_activity([], self.now)
        self.assertEqual(data['status'], 'available')
        self.assertEqual(data['spendUSD'], 0)
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(read_activity(), {'status': 'credential_missing'})

    def test_reuses_existing_credentials_and_management_precedence(self):
        with patch.dict('os.environ', {'OPENROUTER_API_KEY': 'primary',
                                      'OPENROUTER_MANAGEMENT_KEY': 'legacy',
                                      'OPENROUTER_MANAGEMENT_API_KEY': 'standard'}, clear=True):
            self.assertEqual(activity_key(), 'standard')
            with tempfile.TemporaryDirectory() as folder:
                config = Path(folder) / 'config.json'
                config.write_text(json.dumps({'providers': [{'id': 'openrouter', 'pluginSecrets':
                                                 {'OPENROUTER_MANAGEMENT_API_KEY': 'configured'}}]}))
                self.assertEqual(activity_key(config_path=config), 'configured')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(activity_key(primary_key='primary-management'), 'primary-management')

    def test_primary_key_reaches_activity_and_denial_is_not_missing(self):
        with patch.dict('os.environ', {}, clear=True):
            response = io.BytesIO(b'{"data": []}')
            with patch('activity.urllib.request.urlopen', return_value=response) as fetch:
                self.assertEqual(read_activity(primary_key='primary-management')['status'], 'available')
                self.assertEqual(fetch.call_args.args[0].get_header('Authorization'), 'Bearer primary-management')
            for code, status in ((401, 'authentication_failed'), (403, 'permission_denied'), (500, 'unavailable')):
                error = urllib.error.HTTPError('https://openrouter.ai/api/v1/activity', code, 'error', {}, None)
                with patch('activity.urllib.request.urlopen', side_effect=error):
                    self.assertEqual(read_activity(primary_key='primary'), {'status': status})

    def test_invalid_metrics_do_not_become_zero(self):
        for field, value in [('requests', True), ('usage', float('nan')), ('prompt_tokens', -1)]:
            row = self.row('model', 10)
            row[field] = value
            with self.assertRaises(ValueError):
                project_activity([row], self.now)

    def test_api_sql_timestamps_and_iso_dates_share_utc_days(self):
        rows = [self.row('model', 10, '2026-10-04 00:00:00'),
                self.row('model', 20, '2026-10-04T00:00:00Z'),
                self.row('model', 30, '2026-10-04')]
        data = project_activity(rows, self.now)
        self.assertEqual(data['models'][0]['tokens'], 69)
        self.assertEqual(data['days'][-1]['tokens'], [69])
        for date in (None, 'not-a-date'):
            with self.assertRaises(ValueError):
                project_activity([self.row('model', 10, date)], self.now)


if __name__ == '__main__':
    unittest.main()
