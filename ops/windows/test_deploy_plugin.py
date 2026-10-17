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
        seen = []
        def state(name, value):
            seen.append((name, value))
            if name == 'core':
                raise RuntimeError('injected')
        with patch.object(d, 'service_state', state):
            with self.assertRaises(RuntimeError):
                d.start({'mode': 'service', 'running_services': ['core', 'panel', 'keeper']})
        self.assertEqual(seen, [('core', 'Running'), ('panel', 'Running'), ('keeper', 'Running')])

    def test_stop_reverse_dependency_order(self):
        seen = []
        with patch.object(d, 'service_state', lambda n, s: seen.append(n)):
            d.stop({'mode': 'service', 'running_services': ['core', 'panel', 'keeper']})
        self.assertEqual(seen, ['keeper', 'panel', 'core'])

    def test_bad_backup_refuses_before_stop(self):
        with tempfile.TemporaryDirectory() as root:
            p = pathlib.Path(root) / 'old.dll'
            p.write_bytes(b'corrupted')
            m = {'transaction': root, 'files': [{'backup': 'old.dll', 'old_hash': 'not-matching'}]}
            with patch.object(d, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'backup_hash'):
                    d.recover(m)
                stop.assert_not_called()

    def test_rollback_restores_hash_and_records_result(self):
        with tempfile.TemporaryDirectory() as root:
            r = pathlib.Path(root)
            (r / 'old.dll').write_bytes(b'old')
            (r / 'live.dll').write_bytes(b'new')
            m = {'transaction': root, 'files': [{'backup': 'old.dll', 'old_hash': d.digest(r / 'old.dll'), 'target': str(r / 'live.dll')}]}
            with patch.object(d, 'stop'), patch.object(d, 'start'), patch.object(d, 'healthy', return_value=True):
                d.recover(m)
            self.assertEqual((r / 'live.dll').read_bytes(), b'old')
            self.assertIn('rolled_back', (r / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
