import datetime as dt
import unittest
from activity import project_activity, read_activity
from unittest.mock import patch


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
            self.assertEqual(read_activity(), {'status': 'access_required'})

    def test_invalid_metrics_do_not_become_zero(self):
        for field, value in [('requests', True), ('usage', float('nan')), ('prompt_tokens', -1)]:
            row = self.row('model', 10)
            row[field] = value
            with self.assertRaises(ValueError):
                project_activity([row], self.now)


if __name__ == '__main__':
    unittest.main()
