import sys
from pathlib import Path
from unittest import mock
from bzztest import RoundCase, REL
from haivequeen import action_logs, integration

class LogDrainTests(RoundCase):
    def test_unwritable_journal_keeps_draining_and_retains_real_exit(self):
        path=action_logs.start('testproj',REL,'Build')
        script=self.base/'output.py'
        script.write_text("for i in range(2000): print('line-%s '%i+'x'*2048,flush=True)\nraise SystemExit(7)\n")
        original=Path.open
        def open_file(target,*args,**kwargs):
            if str(target)==path and (args[0] if args else kwargs.get('mode','r'))=='a':
                raise OSError('journal unavailable')
            return original(target,*args,**kwargs)
        with mock.patch.object(Path,'open',open_file):
            code,output=integration._watch_compile('"%s" "%s"'%(sys.executable,script),str(self.base),5,10,.01,log_file=path)
            action_logs.finish(path,code)
            records=action_logs.read(path)
        self.assertEqual(code,7)
        self.assertIn('line-1999',output)
        self.assertIn('Log storage error',output)
        self.assertEqual(records[-1]['exit_code'],7)
        self.assertLess(len(output),600000)
