import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('deploy_plugin', pathlib.Path(__file__).with_name('deploy_plugin.py'))
assert SPEC is not None and SPEC.loader is not None
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)


class DeploymentTests(unittest.TestCase):
    def test_service_start_attempts_every_original_service(self):
        with tempfile.TemporaryDirectory() as root:
            seen = []
            def state(name, value):
                seen.append((name, value))
                if name == 'core':
                    raise RuntimeError('injected')
            with patch.object(d, 'service_state', state):
                with self.assertRaises(RuntimeError):
                    d.start({'mode': 'service', 'transaction': root, 'running_services': ['core', 'panel', 'keeper']})
            self.assertEqual(seen, [('core', 'Running'), ('panel', 'Running'), ('keeper', 'Running')])

    def test_stop_reverse_dependency_order(self):
        with tempfile.TemporaryDirectory() as root:
            seen = []
            with patch.object(d, 'service_state', lambda n, s: seen.append(n)), patch.object(
                d, 'snapshot', return_value=[
                    {'name': 'keeper', 'status': 'Stopped'},
                    {'name': 'panel', 'status': 'Stopped'},
                    {'name': 'core', 'status': 'Stopped'},
                ]):
                d.stop({'mode': 'service', 'transaction': root, 'running_services': ['core', 'panel', 'keeper']})
            self.assertEqual(seen, ['keeper', 'panel', 'core'])

    def test_stop_refuses_pending_service_state(self):
        with tempfile.TemporaryDirectory() as root:
            m = {'mode': 'service', 'transaction': root,
                 'running_services': ['LocalCPA', 'Panel']}
            with patch.object(d, 'service_state'), patch.object(
                d, 'snapshot', return_value=[
                    {'name': 'LocalCPA', 'status': 'Stopped'},
                    {'name': 'Panel', 'status': 'StopPending'},
                ]):
                with self.assertRaisesRegex(RuntimeError, 'services_not_stopped'):
                    d.stop(m)
        with tempfile.TemporaryDirectory() as root:
            p = pathlib.Path(root) / 'old.dll'
            p.write_bytes(b'corrupted')
            m = {'mode': 'process', 'transaction': root, 'files': [{'backup': 'old.dll', 'old_hash': 'not-matching'}]}
            with patch.object(d, 'stop') as stop, patch.object(d, 'start') as start:
                self.assertEqual(d.recover(m), 3)
                stop.assert_not_called()
                start.assert_called_once()

    def test_slow_connection_close_has_total_deadline(self):
        import socket
        import threading
        import time
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        def serve():
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n')
                try:
                    for _ in range(100):
                        conn.sendall(b' ')
                        time.sleep(.15)
                except OSError:
                    pass
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        began = time.monotonic()
        try:
            with self.assertRaises(Exception):
                d.request({'port': port}, '/fixture', 'dummy')
            self.assertLess(time.monotonic() - began, 6.5)
        finally:
            listener.close()
            worker.join(2)

    def test_bad_backup_refuses_before_stop(self):
        with tempfile.TemporaryDirectory() as root:
            p = pathlib.Path(root) / 'old.dll'
            p.write_bytes(b'corrupted')
            m = {'mode': 'process', 'transaction': root, 'files': [{'backup': 'old.dll', 'old_hash': 'not-matching'}]}
            with patch.object(d, 'stop') as stop, patch.object(d, 'start') as start:
                self.assertEqual(d.recover(m), 3)
                stop.assert_not_called()
                start.assert_called_once()

    def test_rollback_restores_hash_and_records_result(self):
        with tempfile.TemporaryDirectory() as root:
            r = pathlib.Path(root)
            (r / 'old.dll').write_bytes(b'old')
            (r / 'live.dll').write_bytes(b'new')
            m = {'mode': 'process', 'transaction': root, 'files': [{'backup': 'old.dll', 'old_hash': d.digest(r / 'old.dll'), 'target': str(r / 'live.dll')}]}
            with patch.object(d, 'stop'), patch.object(d, 'start'), patch.object(d, 'healthy', return_value=True):
                d.recover(m)
            self.assertEqual((r / 'live.dll').read_bytes(), b'old')
            self.assertIn('rolled_back', (r / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
