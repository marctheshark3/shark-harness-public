from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from companion import main, runtime_files, service_text, stop_child


class ServiceTests(unittest.TestCase):
    def test_runtime_manifest_includes_assets_and_license_without_private_or_build_files(self):
        included = {'companion.py', 'requirements-device.txt', 'web/app.js',
                    'assets/icon.svg', 'assets/fonts/LICENSE', 'characters/reef.json', 'LICENSE', 'NOTICE'}
        excluded = {'README.md', 'private.token', 'artifacts/capture.wav', 'firmware/main/main.c'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('test fixture')
            manifest = runtime_files(root)
            self.assertEqual({path.relative_to(root).as_posix() for path in manifest}, included)
            self.assertEqual(manifest, sorted(manifest))

    def test_run_and_install_require_an_explicit_device_without_side_effects(self):
        for command in ('run', 'install'):
            with self.subTest(command=command), patch.dict('os.environ', {}, clear=True), \
                    patch('sys.argv', ['companion.py', command]), patch('companion.run') as run, \
                    patch('companion.install') as install, redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)
                self.assertIn('SHARK_DEVICE_PORT', output.getvalue())
                run.assert_not_called()
                install.assert_not_called()

    def test_device_environment_and_explicit_override(self):
        for command in ('run', 'install'):
            for arguments, expected in (([], '/dev/serial/env-device'),
                                        (['--device', '/dev/serial/cli-device'], '/dev/serial/cli-device')):
                with self.subTest(command=command, arguments=arguments), \
                        patch.dict('os.environ', {'SHARK_DEVICE_PORT': '/dev/serial/env-device'}), \
                        patch('sys.argv', ['companion.py', command, *arguments]), \
                        patch('companion.' + command) as action:
                    main()
                    self.assertEqual(action.call_args.args[-1], expected)

    def test_service_management_does_not_require_a_device(self):
        for command in ('start', 'stop', 'restart', 'status'):
            with self.subTest(command=command), patch.dict('os.environ', {}, clear=True), \
                    patch('sys.argv', ['companion.py', command]), \
                    patch('companion.subprocess.run') as systemctl, patch('companion.status') as status, \
                    redirect_stdout(io.StringIO()):
                main()
                if command == 'status':
                    status.assert_called_once_with()
                    systemctl.assert_not_called()
                else:
                    self.assertEqual(systemctl.call_args.args[0],
                                     ['systemctl', '--user', command, 'shark-companion.service'])

    def test_service_follows_t3_and_quotes_paths(self):
        text = service_text('/tools with spaces/python', '/code/companion.py', '/dev/serial/my%device')
        self.assertIn('After=t3code.service', text)
        self.assertIn('PartOf=t3code.service', text)
        self.assertIn('WantedBy=default.target t3code.service', text)
        self.assertIn('"/tools with spaces/python"', text)
        self.assertIn('"/dev/serial/my%%device"', text)
        self.assertIn('UMask=0077', text)

    def test_stop_gives_microphone_cleanup_time_before_termination(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('device', 4), None]
        stop_child(child)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.terminate.assert_called_once_with()
        child.kill.assert_not_called()
