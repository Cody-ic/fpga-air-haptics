import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.build_final_software import ROOT, load_profile, validate_calibration
from tools import build_final_software as final


class FinalSoftwareTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile(ROOT/'release/software.json')

    def test_release_cannot_select_zero_correction_or_preview(self):
        for key, value in [('calibration_required', False), ('auto_start', True),
                           ('expected_cal_profile', 0), ('expected_preview_only', 1)]:
            profile = copy.deepcopy(self.profile)
            profile['transmitter'][key] = value
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'profile.json'
                path.write_text(json.dumps(profile), encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_profile(path)

    def test_missing_measured_data_never_falls_back_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, '不能用零补偿替代'):
                validate_calibration(self.profile, Path(tmp)/'missing.json')

    def test_registered_measurement_level_and_reference_reach_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'registered.json'
            path.write_bytes(b'validator fixture, not a measured calibration')
            self.profile['calibration']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch('tools.build_final_software.load_restore', return_value=(123, {'verified': True})) as load:
                _, info = validate_calibration(self.profile, path)
                load.assert_called_once_with(path.resolve(), 50, 0, 20)
                self.assertTrue(info['verified'])

    def test_wrong_file_rejected_before_measurement_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'wrong.json'
            path.write_bytes(b'{}')
            with patch('tools.build_final_software.load_restore') as load:
                with self.assertRaisesRegex(ValueError, '校验值'):
                    validate_calibration(self.profile, path)
                load.assert_not_called()

    def test_prepare_passes_recorded_level_and_reference_to_display_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calibration = root/'registered.json'
            with patch.object(final, 'validate_calibration', return_value=(calibration, {})), \
                    patch.object(final.build_display_restore, 'main') as build:
                final.main(['--profile', str(ROOT/'release/software.json'),
                            '--calibration-mode', 'restore', '--output-root', str(root), '--prepare-only'])
            argv = build.call_args.args[0]
            self.assertEqual(argv[argv.index('--calibration-z-mm')+1], '50')
            self.assertEqual(argv[argv.index('--calibration-level')+1], '20')
            self.assertEqual(argv[argv.index('--calibration-reference')+1], '0')
            self.assertEqual(argv[argv.index('--calibration-file')+1], str(calibration))
            self.assertIn('--prepare-only', argv)

    def test_default_keeps_full_solver_without_a_measured_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(final, 'validate_calibration') as restore, \
                patch.object(final.build_display_restore, 'main') as build:
            final.main(['--output-root', tmp, '--prepare-only'])
            restore.assert_not_called()
            argv = build.call_args.args[0]
            self.assertEqual(argv[argv.index('--startup-calibration')+1], 'adc')
            self.assertEqual(argv[argv.index('--calibration-level')+1], '20')
            self.assertNotIn('--uncalibrated', argv)
            self.assertNotIn('--calibration-file', argv)

    def test_existing_manifest_cannot_relabel_a_zero_calibration_image(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(final.build_display_restore, 'main') as build:
            manifest = Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps(dict(mode='display_uncalibrated', auto_start=False)), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '不符合'):
                final.main(['--existing-manifest', str(manifest), '--output-root', tmp])
            build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
