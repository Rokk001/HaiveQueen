import http.client
import socket
import threading
import unittest
from unittest.mock import patch
from haivequeen import web_service


class ControlTimeoutTests(unittest.TestCase):
    def test_incomplete_headers_release_control_for_status_reload_and_stop(self):
        for payload in (b'', b'POST /status HTTP/1.1\r\nHost: localhost\r\n'):
            with self.subTest(payload=payload):
                service = web_service.Service()
                service.control.request_timeout = 0.1
                accepted = threading.Event()
                original = service.control.get_request

                def accept():
                    result = original()
                    accepted.set()
                    return result

                done = threading.Event()
                def serve():
                    while not done.is_set():
                        service.control.handle_request()

                with patch.object(service.control, 'get_request', side_effect=accept), patch.object(service, 'reload') as reload:
                    worker = threading.Thread(target=serve)
                    worker.start()
                    idle = socket.create_connection(service.control.server_address, timeout=2)
                    try:
                        if payload:
                            idle.sendall(payload)
                        self.assertTrue(accepted.wait(2))
                        for action in ('status', 'reload', 'stop'):
                            client = http.client.HTTPConnection(*service.control.server_address, timeout=1)
                            try:
                                client.request('POST', '/'+action, headers={'Authorization':'Bearer '+service.token})
                                response = client.getresponse()
                                self.assertEqual(response.status, 200)
                                response.read()
                            finally:
                                client.close()
                        reload.assert_called_once()
                        self.assertTrue(service.stopping)
                        self.assertEqual(idle.recv(1), b'')
                    finally:
                        done.set()
                        idle.close()
                        worker.join(3)
                        service.control.server_close()
                        self.assertFalse(worker.is_alive())
