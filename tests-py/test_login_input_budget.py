import threading
import unittest
from types import SimpleNamespace
from unittest import mock
from haivequeen import web_login

class LoginInputTests(unittest.TestCase):
    def test_blocked_input_does_not_block_status_close_or_more_requests(self):
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        def write(data):
            entered.set()
            release.wait(5)
            return len(data)
        process=mock.Mock(pid=123, stdin=SimpleNamespace(write=write,flush=lambda:None))
        process.poll.return_value=None
        run={'provider':'fixture','process':process,'output':'ready','complete':False}
        errors=[]
        def send():
            try: web_login.send('fixture','code')
            except ValueError as error: errors.append(str(error))
            finally: returned.set()
        with mock.patch.dict(web_login._runs,{'fixture':run},clear=True), mock.patch.object(web_login,'INPUT_TIMEOUT',.05,create=True), mock.patch('haivequeen.platform.kill_process_tree') as kill:
            worker=threading.Thread(target=send)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(returned.wait(.5),'A blocked pipe must not hold the HTTP request')
                self.assertTrue(web_login.status('fixture')['running'])
                with self.assertRaisesRegex(ValueError,'pending'):
                    web_login.send('fixture','another')
                web_login.close()
                kill.assert_called_once_with(123)
                self.assertIn('pending',errors[0])
            finally:
                release.set()
                worker.join(2)
                if run.get('input_done'): run['input_done'].wait(2)
