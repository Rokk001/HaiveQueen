"""Black-box regressions for atomic live configuration reloads."""
import copy
import os
from unittest import mock

from bzztest import RoundCase, runtime
from haivequeen import config, loop
from haivequeen import scheduler as sch


# Change only disposable configuration files while observing loop rounds.
class LiveConfigTest(RoundCase):
    """Reload settings without starting a provider or changing an active agent."""

    # Put the initial machine and project settings on disk.
    def setUp(self):
        """Start each test with real temporary TOML and a long idle interval."""
        super().setUp()
        self.machine_file = config.config_dir() / 'config.toml'
        self.machine_file.parent.mkdir(parents=True, exist_ok=True)
        self.project_file = self.repo / '.haivequeen' / 'project.toml'
        self.write_machine()
        self.write_project()
        self.machine = config.load_config()

    # Write enough provider configuration to verify slots and class changes.
    def write_machine(self, slots=1, model='old-model', minutes=60):
        """Write a complete small machine config under BZZ_CONFIG_DIR."""
        self.machine_file.write_text(
            "slots = %d\nroundMinutes = %s\n[[providers]]\nname = 'claude'\nprotocol = 'claude'\nslots = %d\n"
            "[providers.classes.medium]\nmodel = '%s'\neffort = 'medium'\n" % (slots, minutes, slots, model), encoding='utf-8')

    # Preserve normal project identity while changing supported overrides.
    def write_project(self, queen='medium', extra=''):
        """Write a project override with one plan glob and optional extra keys."""
        self.project_file.write_text("name = 'testproj'\nplanGlobs = ['docs/plans/**/*.md']\nbuildCommand = '%s'\n%s" % (queen, extra), encoding='utf-8')

    # Drive the real loop using deterministic idle polls and inert boundaries.
    def drive(self, change, max_polls=4, round_minutes=0):
        """Capture two rounds or stop after bounded fake polling."""
        self.rounds_seen, self.idles, self.polls = [], [], 0
        tick = [1700000000.0]
        original_write = loop.write_state

        # Capture independent snapshots so later mutations cannot change evidence.
        def round_call(conn, machine, envs, **kwargs):
            """Observe accepted machine and project settings without scheduling."""
            self.rounds_seen.append((copy.deepcopy(machine), {e.name: copy.deepcopy(e.settings) for e in envs}))

        # Advance time and let the test rewrite temporary files during an idle poll.
        def sleeper(seconds):
            """Bound the loop even when the old implementation misses reloads."""
            self.polls += 1
            tick[0] += seconds
            change(self.polls)
            if self.polls >= max_polls:
                loop.stop_flag().write_text('test finished', encoding='utf-8')

        # Keep normal state writes while recording advertised scheduling deadlines.
        def write(conn, status, since, next_at):
            """Record the configured idle duration at every completed round."""
            if status == 'idle' and next_at is not None:
                self.idles.append(next_at.timestamp() - tick[0])
            return original_write(conn, status, since, next_at)

        with mock.patch.object(loop, 'run_round', side_effect=round_call), \
                mock.patch.object(loop, 'daily_export'), \
                mock.patch.object(loop.usage, 'refresh_claude_usage'), \
                mock.patch.object(loop.reattach, 'reattach', return_value={'kept': [], 'failed': []}), \
                mock.patch.object(loop, 'active_count', return_value=0), \
                mock.patch.object(loop.time, 'time', side_effect=lambda: tick[0]), \
                mock.patch.object(loop, 'write_state', side_effect=write), \
                mock.patch.object(loop.osplat, 'kill_process_tree') as kill:
            result = loop._run_loop(self.conn, self.machine, 2, round_minutes, sleeper, 2, {})
        self.assertEqual(result, 0)
        kill.assert_not_called()

    # Changed slots and class settings apply only to subsequent work.
    def test_machine_changes_wake_and_keep_running_agent(self):
        """Accept changed capacity and model without altering the running row."""
        self.agent('busy', status='running', pid=1234, model='running-model', started=1)
        before = runtime.get_agent(self.conn, 'testproj', 'busy')
        for slots in (0, 2):
            with self.subTest(slots=slots):
                self.write_machine()
                self.machine = config.load_config()
                self.drive(lambda poll: self.write_machine(slots=slots, model='new-model') if poll == 1 else None)
                self.assertEqual(len(self.rounds_seen), 2)
                self.assertEqual(self.polls, 1)
                machine, envs = self.rounds_seen[1]
                self.assertEqual(machine['providers'][0]['slots'], slots)
                self.assertEqual(envs['testproj']['providers'][0]['classes']['medium']['model'], 'new-model')
                after = runtime.get_agent(self.conn, 'testproj', 'busy')
                for field in ('pid', 'model', 'status', 'name'):
                    self.assertEqual(after.get(field), before.get(field))
                self.assertEqual(sch.provider_free(sch.get_providers(machine), [after])['claude'], max(0, slots - 1))

    # Project overrides reload without overriding protected machine settings.
    def test_project_changes_keep_machine_only_slots(self):
        """A changed Queen class wakes scheduling while project slots stay ignored."""
        self.drive(lambda poll: self.write_project('heavy', 'slots = 999\n') if poll == 1 else None)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.rounds_seen[1][1]['testproj']['buildCommand'], 'heavy')
        self.assertEqual(self.rounds_seen[1][1]['testproj']['slots'], 1)

    # Configuration-based round intervals update after an accepted reload.
    def test_round_minutes_reload_updates_next_deadline(self):
        """Changing a long interval wakes immediately and sets the new interval."""
        self.drive(lambda poll: self.write_machine(minutes=3) if poll == 1 else None)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.idles, [3600, 180])

    # An explicit command-line interval remains stronger than a config change.
    def test_explicit_round_minutes_remain_authoritative(self):
        """Reloading config cannot replace the caller's explicit round interval."""
        self.drive(lambda poll: self.write_machine(minutes=3) if poll == 1 else None, round_minutes=2)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.idles, [120, 120])

    # Reject the whole candidate when either configuration file cannot be parsed.
    def test_malformed_toml_retains_snapshot_and_logs_once(self):
        """Valid edits wait for machine or project repair without repeated diagnostics."""
        for broken in ('machine', 'project'):
            with self.subTest(broken=broken):
                self.write_machine()
                self.write_project()
                self.machine = config.load_config()
                earlier = len(runtime.list_events(self.conn, 'testproj'))

                # Leave the same invalid file in place for two consecutive polls.
                def change(poll):
                    """Repair the bad file after proving no partial round was run."""
                    if poll == 1:
                        self.write_machine(slots=4)
                        self.write_project('heavy')
                        target = self.machine_file if broken == 'machine' else self.project_file
                        target.write_text('invalid = [', encoding='utf-8')
                    elif poll == 3:
                        self.assertEqual(len(self.rounds_seen), 1)
                        if broken == 'machine':
                            self.write_machine(slots=4)
                        else:
                            self.write_project('heavy')
                self.drive(change)
                self.assertEqual(len(self.rounds_seen), 2)
                self.assertEqual(self.polls, 3)
                self.assertEqual(self.rounds_seen[1][0]['providers'][0]['slots'], 4)
                self.assertEqual(self.rounds_seen[1][1]['testproj']['buildCommand'], 'heavy')
                messages = [e['text'] for e in runtime.list_events(self.conn, 'testproj')[earlier:]]
                errors = [s for s in messages if 'toml' in s.lower() and 'reload' in s.lower() and any(word in s.lower() for word in ('error', 'failed', 'invalid'))]
                self.assertEqual(len(errors), 1, messages)

    # Disappearance of an existing file must not activate defaults.
    def test_missing_existing_machine_retains_snapshot_until_restored(self):
        """Treat a missing established config as an incomplete edit, then recover."""
        moved = self.machine_file.with_suffix('.held')
        # Keep the original bytes available while simulating a missing config.
        def change(poll):
            """Restore a valid changed config after one missing-file poll."""
            if poll == 1:
                self.machine_file.rename(moved)
                self.write_project('heavy')
            elif poll == 2:
                self.assertEqual(len(self.rounds_seen), 1)
                self.write_machine(slots=3)
        self.drive(change)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 2)
        self.assertEqual(self.rounds_seen[1][0]['providers'][0]['slots'], 3)

    # An absent initial machine file must preserve caller-supplied settings.
    def test_initially_absent_machine_keeps_synthetic_settings(self):
        """Project reload does not replace supplied providers with defaults."""
        self.machine_file.rename(self.machine_file.with_suffix('.held'))
        self.machine['slots'] = 7
        self.drive(lambda poll: self.write_project('heavy') if poll == 1 else None)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.rounds_seen[1][0]['slots'], 7)
        self.assertEqual(self.rounds_seen[1][1]['testproj']['buildCommand'], 'heavy')

    # Touching a file without changing values is not a scheduling event.
    def test_same_values_do_not_force_an_extra_round(self):
        """A semantically identical file rewrite leaves the old idle timer intact."""
        self.drive(lambda poll: self.machine_file.write_text(self.machine_file.read_text(encoding='utf-8') + '\n', encoding='utf-8') if poll == 1 else None, max_polls=3)
        self.assertEqual(len(self.rounds_seen), 1)

    # Changes to registered project membership join the next scheduling snapshot.
    def test_new_registered_project_wakes_idle_loop(self):
        """A newly registered project appears without restarting the controller."""
        self.drive(lambda poll: self.add_project('beta') if poll == 1 else None)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 1)
        self.assertIn('beta', self.rounds_seen[1][1])

    # Changed content must be noticed even when size and timestamp are preserved.
    def test_equal_size_changed_values_with_preserved_timestamp_wake(self):
        """Read actual TOML changes instead of relying on file metadata alone."""
        previous = self.machine_file.stat()

        # Preserve both timestamps after an equal-length model edit.
        def change(poll):
            """Simulate an editor preserving the original modification timestamp."""
            if poll == 1:
                self.write_machine(model='new-model')
                self.assertEqual(self.machine_file.stat().st_size, previous.st_size)
                os.utime(self.machine_file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        self.drive(change)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 1)
        self.assertEqual(self.rounds_seen[1][0]['providers'][0]['classes']['medium']['model'], 'new-model')

    # Do not apply a candidate when its files change during validation.
    def test_file_change_during_validation_retries_next_poll(self):
        """Retain the old snapshot until a stable final config can be accepted."""
        original_load = config.load_config
        changed_during_read = [False]

        # Change the file once after parsing the intermediate candidate.
        def load():
            """Return intermediate values while putting different bytes on disk."""
            candidate = original_load()
            if candidate['providers'][0]['slots'] == 2 and not changed_during_read[0]:
                changed_during_read[0] = True
                self.write_machine(slots=3)
            return candidate

        # The first idle read races; only the next stable poll may wake a round.
        def change(poll):
            """Write the intermediate file and reject any early scheduling wake."""
            if poll == 1:
                self.write_machine(slots=2)
            elif poll == 2:
                self.assertEqual(len(self.rounds_seen), 1)
        with mock.patch.object(config, 'load_config', side_effect=load):
            self.drive(change)
        self.assertTrue(changed_during_read[0])
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 2)
        self.assertEqual(self.rounds_seen[1][0]['providers'][0]['slots'], 3)

    # Valid TOML with an unusable numeric value must not stop the running loop.
    def test_infinite_hive_count_retains_snapshot_and_recovers(self):
        """Reject infinite capacity once and accept the following valid edit."""
        # Keep the invalid value unchanged across two idle polls before repair.
        def change(poll):
            """Write legal TOML infinity, then restore usable capacity."""
            if poll == 1:
                text = self.machine_file.read_text(encoding='utf-8')
                self.machine_file.write_text('hives = inf\n' + text, encoding='utf-8')
            elif poll == 3:
                self.assertEqual(len(self.rounds_seen), 1)
                self.write_machine(slots=2)
        self.drive(change)
        self.assertEqual(len(self.rounds_seen), 2)
        self.assertEqual(self.polls, 3)
        self.assertEqual(self.rounds_seen[1][0]['providers'][0]['slots'], 2)
        messages = [e['text'] for e in runtime.list_events(self.conn, 'testproj')]
        errors = [s for s in messages if 'toml' in s.lower() and 'reload' in s.lower() and any(word in s.lower() for word in ('error', 'failed', 'invalid'))]
        self.assertEqual(len(errors), 1, messages)
