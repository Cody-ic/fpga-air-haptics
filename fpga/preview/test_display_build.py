"""Generation checks only: no vendor tools, serial ports or board access."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import build_display_restore as build
import check_display_restore as check
import generate_assets as assets


class DisplayBuildTests(unittest.TestCase):
    def test_startup_adc_retains_solver_without_restoring_or_emitting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(build, 'generate') as generate, patch.object(build, 'load_restore') as restore, \
                    contextlib.redirect_stdout(io.StringIO()):
                build.main(['--startup-calibration', 'adc', '--calibration-z-mm', '50',
                            '--calibration-level', '20', '--output-root', tmp, '--prepare-only'])
            restore.assert_not_called()
            generate.assert_called_once_with(Path(tmp)/'startup_adc_z50/src', preview_only=False,
                                             startup_calibration=True)
            source = Path(tmp)/'startup_adc_z50/src'
            board = (source/'board_top.v').read_text(encoding='utf-8')
            self.assertIn('`define FPGA_CALIBRATION_ENABLE 1', board)
            self.assertIn('`define FPGA_CALIBRATION_SOURCE 1', board)
            self.assertIn('`define FPGA_DEBUG_U102 0', board)
            top = (source/'calibration_display_top.v').read_text(encoding='utf-8')
            self.assertIn('.CAL_RESTORE_ENABLE(0)', top)
            self.assertIn('.CAL_Z_UM(50000),.CAL_LEVEL(20)', top)
            self.assertIn(".CAL_RESTORE_OFFSETS(512'h"+'0'*128, top)
            constraints = (source/'display.cst').read_text(encoding='utf-8')
            for lane, row in enumerate((35, 47, 33, 29)):
                self.assertIn(f'INS_LOC "reset_driver/lane[{lane}].reset_ff" R{row}C180[0][A]', constraints)

    def test_startup_scope_and_restore_are_explicit_exclusive_modes(self):
        self.assertEqual(build.parse_args(['--startup-calibration', 'scope']).calibration_z_mm, 50)
        for argv in (['--startup-calibration', 'adc', '--uncalibrated'],
                     ['--startup-calibration', 'scope', '--calibration-file', 'file.json'],
                     ['--startup-calibration', 'adc', '--calibration-reference', '3']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    build.parse_args(argv)

    def test_checker_startup_uses_wait_and_observation_bench_not_a_fake_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'startup_adc_z50/src'
            source.mkdir(parents=True)
            (source/'fixture.v').write_text('// source fixture')
            with patch.object(check, 'load_restore') as restore, patch.object(check, 'generate'), \
                    patch.object(check, 'simulate') as simulate:
                check.main(['--startup-calibration', 'adc', '--calibration-z-mm', '50',
                            '--calibration-level', '20', '--output-root', tmp])
            restore.assert_not_called()
            self.assertEqual(simulate.call_count, 1)
            self.assertEqual(simulate.call_args.args[2], 'tb_startup_display')
            bench = simulate.call_args.args[3]
            self.assertIn('.CAL_RESTORE_ENABLE(0)', bench)
            self.assertIn('.CAL_Z_UM(50000),.CAL_LEVEL(20)', bench)
            self.assertIn('wrong READY started measurement', bench)
            self.assertIn('CAL2 output status hidden or fabricated field shown', bench)

    def test_checker_uses_explicit_measurement_settings_in_every_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'z70'/'src'
            source.mkdir(parents=True)
            (source/'fixture.v').write_text('// source fixture, no hardware')
            info = {'expected_center_phases': [0]*64}
            with patch.object(check, 'load_restore', return_value=(0x1234, info)) as restore, \
                    patch.object(check, 'generate'), patch.object(check, 'simulate') as simulate:
                check.main(['--calibration-file', 'fixture.json', '--calibration-z-mm', '70',
                            '--calibration-level', '20', '--calibration-reference', '3',
                            '--output-root', str(root)])
            restore.assert_called_once_with('fixture.json', 70, 3, 20)
            self.assertEqual(simulate.call_count, 2)
            bench = simulate.call_args.args[3]
            self.assertIn('.CAL_Z_UM(70000)', bench)
            self.assertIn('z!==70000', bench)
            self.assertNotIn('TARGET_Z_UM', bench)
            self.assertEqual(simulate.call_args.args[4], [source/'fixture.v'])

    def test_checker_current_release_and_uncalibrated_options(self):
        args = check.parse_args(['--calibration-file', 'fixture.json', '--calibration-level', '20',
                                 '--calibration-reference', '0'])
        self.assertEqual((args.calibration_z_mm, args.calibration_level, args.calibration_reference), (50, 20, 0))
        self.assertTrue(check.parse_args(['--uncalibrated']).uncalibrated)
        for argv in (['--uncalibrated', '--calibration-level', '20'],
                     ['--uncalibrated', '--calibration-reference', '3'],
                     ['--calibration-file', 'fixture.json', '--calibration-level', '0']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    check.parse_args(argv)

    def test_explicit_current_measurement_reaches_display_prepare(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(build, 'generate'), \
                    patch.object(build, 'load_restore', return_value=(0x1234, {})) as restore, \
                    contextlib.redirect_stdout(io.StringIO()):
                build.main(['--calibration-file', 'registered.json', '--calibration-z-mm', '50',
                            '--calibration-level', '20', '--calibration-reference', '0',
                            '--output-root', tmp, '--prepare-only'])
            restore.assert_called_once_with('registered.json', 50, 0, 20)
            top = (Path(tmp)/'z50/src/calibration_display_top.v').read_text(encoding='utf-8')
            self.assertIn('.CAL_Z_UM(50000)', top)
            self.assertIn('.CAL_RESTORE_ENABLE(1)', top)

    def test_restore_top_remains_default(self):
        top = build.make_top(0x1234, 50)
        self.assertIn('.CAL_RESTORE_ENABLE(1)', top)
        self.assertIn(".CAL_RESTORE_OFFSETS(512'h" + f'{0x1234:0128x}', top)
        self.assertIn('.EXTERNAL_CONTROL(1)', top)
        self.assertIn('`define FPGA_VIDEO_LINK_TEST 0', top)
        self.assertNotIn('assign sr_oe_n=1;', top)

    def test_uncalibrated_top_cannot_embed_corrections(self):
        top = build.make_top((1 << 512) - 1, 70, restore=False)
        self.assertIn('.CAL_RESTORE_ENABLE(0)', top)
        self.assertIn(".CAL_RESTORE_OFFSETS(512'h" + '0' * 128, top)
        self.assertIn('.key_next(1\'b0),.key_play(1\'b0)', top)

    def test_cli_requires_an_explicit_exclusive_mode(self):
        invalid = [[], ['--uncalibrated', '--calibration-file', 'unused.json'],
                   ['--calibration-file', 'unused.json'],
                   ['--uncalibrated', '--calibration-z-mm', '50']]
        for argv in invalid:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    build.parse_args(argv)
                self.assertEqual(error.exception.code, 2)
        self.assertTrue(build.parse_args(['--uncalibrated']).uncalibrated)

    def test_prepare_both_modes_in_separate_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(build, 'generate') as generate, \
                    patch.object(build, 'load_restore', return_value=(0x1234, {'fixture': True})) as restore, \
                    contextlib.redirect_stdout(io.StringIO()):
                build.main(['--uncalibrated', '--output-root', str(root), '--prepare-only'])
                restore.assert_not_called()
                build.main(['--calibration-file', 'fixture.json', '--calibration-z-mm', '50',
                            '--output-root', str(root), '--prepare-only'])
                restore.assert_called_once_with('fixture.json', 50, 0, 30)
            self.assertEqual(generate.call_count, 2)
            self.assertTrue(all(call.kwargs == {'preview_only': False} for call in generate.call_args_list))
            for directory, enabled, project in [('uncalibrated', 0, 'display_uncalibrated'),
                                                 ('z50', 1, 'display_restore')]:
                with self.subTest(directory=directory):
                    source = root / directory / 'src'
                    board = (source / 'board_top.v').read_text(encoding='utf-8')
                    self.assertIn(f'`define FPGA_CALIBRATION_ENABLE {enabled}', board)
                    self.assertIn('`define FPGA_CALIBRATION_SOURCE 0', board)
                    self.assertIn('`define FPGA_DEBUG_U102 0', board)
                    self.assertIn('parameter integer BUTTON_FOCUS_ENABLE = 0', board)
                    self.assertIn('parameter integer HW_ROWS       = 8', board)
                    self.assertIn('parameter integer HW_COLS       = 8', board)
                    top = (source / 'calibration_display_top.v').read_text(encoding='utf-8')
                    self.assertIn(f'.CAL_RESTORE_ENABLE({enabled})', top)
                    script = (root / directory / 'build.tcl').read_text(encoding='utf-8')
                    self.assertIn(f'create_project -name {project}', script)
                    self.assertIn('-device_version B', script)
                    self.assertFalse((root / directory / 'manifest.json').exists())

    def test_asset_labels_preserve_preview_and_identify_combined(self):
        with tempfile.TemporaryDirectory() as tmp:
            for preview_only in (True, False):
                draw = Mock()
                with patch.object(assets.ImageDraw, 'Draw', return_value=draw), \
                        patch.object(assets.ImageFont, 'truetype'):
                    if preview_only:
                        assets.generate(Path(tmp) / 'preview')
                    else:
                        assets.generate(Path(tmp) / 'combined', preview_only=False)
                labels = [call.args[1] for call in draw.text.call_args_list]
                expected = '仅预览 · 实际发射关闭' if preview_only else '8×8 阵列控制 / 理论声场'
                self.assertIn(expected, labels)
                if not preview_only:
                    self.assertNotIn('仅预览 · 实际发射关闭', labels)
            self.assertEqual((Path(tmp) / 'preview/sine.hex').read_bytes(),
                             (Path(tmp) / 'combined/sine.hex').read_bytes())


if __name__ == '__main__':
    unittest.main()
