from test_agent_runner import RunnerBase, codex_stream


class ProjectStallTests(RunnerBase):
    def check_stall(self, machine, project, expected):
        self.write_config('agentStallSeconds = %s' % machine)
        if project is not None:
            settings = self.ws / '.haivequeen'
            settings.mkdir()
            (settings / 'project.toml').write_text('agentStallSeconds = %s\n' % project, encoding='utf-8')
        self.prepare('STALL', provider='codex')
        result = self.run_runner('STALL', 'codex', codex_stream(), env={'FAKE_HANG': '2'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual('Stalled: no output' in result.stdout, expected, result.stdout + result.stderr)
        self.assertEqual(self.row('STALL')['status'], 'done')
        self.assertEqual(self.row('STALL')['exit_code'], 0)

    def test_project_enables_stall_detection_over_disabled_machine(self):
        self.check_stall(0, 0.2, True)

    def test_project_zero_disables_machine_stall_detection(self):
        self.check_stall(0.2, 0, False)

    def test_project_longer_limit_prevents_premature_stall(self):
        self.check_stall(0.2, 60, False)

    def test_machine_limit_applies_without_project_override(self):
        self.check_stall(0.2, None, True)
