"""Keep Queen report timestamps independent of scheduler start counters."""
from unittest import mock

from bzztest import REL, RoundCase, minutes_ago
from haivequeen import hives


# Exercise recovery from ended Queens with and without provider capacity.
class HiveStartCountTests(RoundCase):
    """Start counts and fault monitoring reflect actual launches only."""

    # Persist an ended Queen whose Hive still carries the previous running state.
    def ended_hive(self, started_at):
        """Prepare the report-recovery path without starting any real process."""
        self.plan('IN_PROGRESS', [{'id': 'P1', 'title': 'First'}])
        hives.create(self.conn, 'testproj', REL, 'claude', '123456')
        self.conn.execute("UPDATE hives SET state='running'")
        self.agent('previous', phase='queen', status='failed', started_at=started_at,
                   ended=1, pid=None, interrupted='vanished', last_line='runner process vanished')

    # A finished Queen's timestamp must not appear as a successful launch count.
    def test_no_capacity_reports_zero_starts_and_fault_count(self):
        """A round with no free slot reports zero actions to fault monitoring."""
        self.ended_hive(minutes_ago(10))
        for provider in self.machine['providers']:
            provider['slots'] = 0
        with mock.patch('haivequeen.faults.check_round') as check:
            result = self.round()
        self.assertEqual(result['projects']['testproj']['started'], 0)
        self.assertEqual(result['projects']['testproj']['actions'], 0)
        check.assert_called_once()
        self.assertEqual(check.call_args.args[1], 0)
        self.assertIn('round done: 0 Queen(s) started', self.log())

    # A real replacement launch contributes one rather than a timestamp plus one.
    def test_one_replacement_reports_one_start(self):
        """A successfully launched replacement counts once throughout the result."""
        self.ended_hive(minutes_ago(10))
        with mock.patch('haivequeen.faults.check_round') as check:
            result = self.round()
        self.assertEqual(result['projects']['testproj']['started'], 1)
        self.assertEqual(result['projects']['testproj']['actions'], 1)
        check.assert_called_once()
        self.assertEqual(check.call_args.args[1], 1)

    # Legacy records can lack a parseable timestamp without breaking round output.
    def test_missing_previous_timestamp_keeps_zero_integer_count(self):
        """A missing report timestamp never turns the start count into infinity."""
        self.ended_hive(None)
        for provider in self.machine['providers']:
            provider['slots'] = 0
        result = self.round()
        self.assertEqual(result['projects']['testproj']['started'], 0)
        self.assertIn('round done: 0 Queen(s) started', self.log())

    # Invalid imported timestamps must retain the same safe report fallback.
    def test_invalid_previous_timestamp_keeps_zero_integer_count(self):
        """An invalid timestamp does not abort the scheduler's completion path."""
        self.ended_hive('not a timestamp')
        for provider in self.machine['providers']:
            provider['slots'] = 0
        result = self.round()
        self.assertEqual(result['projects']['testproj']['started'], 0)
        self.assertEqual(result['projects']['testproj']['actions'], 0)
