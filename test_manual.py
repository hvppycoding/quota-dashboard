import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from manual import manual_claude_reset_tickets


class ManualTests(unittest.TestCase):
    def test_private_fields_stay_private_and_expiration_is_start_of_day_kst(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'manual.json'
            file.write_text(json.dumps({'claude': {'resetTickets': {
                'type': 'full', 'count': 1, 'expiresOn': '2027-01-12', 'private_note': 'keep-private'}}}))
            on_date = dt.datetime(2027, 1, 11, 14, 59, tzinfo=dt.timezone.utc)
            note = manual_claude_reset_tickets(file, on_date)
            self.assertEqual(note['source'], 'manual')
            self.assertNotIn('private_note', note)
            self.assertEqual(note['expiresAt'], '2027-01-11T15:00:00Z')
            self.assertEqual(note['expirationAssumption'], 'start_of_day_kst')
            self.assertFalse(note['expirationPassed'])
            next_date = on_date + dt.timedelta(minutes=1)
            self.assertTrue(manual_claude_reset_tickets(file, next_date)['expirationPassed'])

    def test_missing_and_invalid_notes_do_not_claim_zero_or_break_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / 'manual.json'
            self.assertIsNone(manual_claude_reset_tickets(file, dt.datetime.now(dt.timezone.utc)))
            file.write_text('invalid json')
            self.assertIsNone(manual_claude_reset_tickets(file, dt.datetime.now(dt.timezone.utc)))
            file.write_text(json.dumps({'claude': {'resetTickets': {
                'type': 'full', 'count': True, 'expiresOn': '2027-01-12'}}}))
            self.assertIsNone(manual_claude_reset_tickets(file, dt.datetime.now(dt.timezone.utc)))


if __name__ == '__main__':
    unittest.main()
