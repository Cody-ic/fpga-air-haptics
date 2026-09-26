"""黄金向量导出工具的格式与数值契约测试。

导出结果写在被忽略的 `desktop_app/.runtime/test-golden/`，便于人工核对。
"""

import hashlib
import json
from pathlib import Path
import unittest

import numpy as np

from desktop_app.golden import frames_per_lap, generate, parse_array, write_outputs
from desktop_app.model import ArraySpec, Config, focus_phases, trajectory_point

OUTPUT_DIR = Path(__file__).resolve().parents[1] / ".runtime" / "test-golden"


class GoldenVectorTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(shape="CIRCLE", radius_um=20000, repeat_millihz=500)
        self.array = ArraySpec(4, 4, 10000, "ROW_MAJOR_XY")
        self.rate = 1000.0
        self.out = OUTPUT_DIR
        self.prefix = OUTPUT_DIR.name

    def exported(self, suffix):
        return self.out / f"{self.prefix}{suffix}"


class ArgumentTests(GoldenVectorTests):
    def test_array_notation(self):
        self.assertEqual(parse_array("8x8"), (8, 8))
        self.assertEqual(parse_array("4, 4"), (4, 4))
        for bad in ("8", "8x8x8", "0x4", "17x4", "axb"):
            with self.assertRaises(Exception):
                parse_array(bad)

    def test_one_lap_frame_count(self):
        self.assertEqual(frames_per_lap(self.config, self.rate), 2000)
        self.assertEqual(frames_per_lap(self.config, 2000.0), 4000)

    def test_generate_rejects_bad_frames(self):
        with self.assertRaises(ValueError):
            generate(self.config, self.array, self.rate, 0)
        with self.assertRaises(ValueError):
            generate(self.config, self.array, 0.0, 4)


class VectorTests(GoldenVectorTests):
    def test_codes_and_focus_match_the_reference_model(self):
        times, focus_mm, codes = generate(self.config, self.array, self.rate, 8)
        np.testing.assert_allclose(times, np.arange(8) / self.rate)
        for index in range(8):
            np.testing.assert_allclose(focus_mm[index],
                                       trajectory_point(self.config, times[index]))
            np.testing.assert_array_equal(codes[index],
                                          focus_phases(self.config, focus_mm[index], self.array))

    def test_every_emitter_gets_its_own_code(self):
        _, _, codes = generate(self.config, self.array, self.rate, 64)
        self.assertEqual(codes.shape, (64, 16))
        self.assertGreater(len({tuple(row) for row in codes}), 8)
        self.assertLessEqual(int(codes.max()), self.config.phase_steps - 1)

    def test_smaller_array_reuses_the_same_coordinates(self):
        small = ArraySpec(2, 2, 10000, "ROW_MAJOR_XY")
        _, focus_mm, codes = generate(self.config, small, self.rate, 4)
        np.testing.assert_allclose(focus_mm[0][:2], trajectory_point(self.config, 0.0)[:2])
        self.assertEqual(codes.shape, (4, 4))


class OutputFileTests(GoldenVectorTests):
    def setUp(self):
        super().setUp()
        self.times, self.focus_mm, self.codes = generate(self.config, self.array, self.rate, 12)
        self.meta = write_outputs(self.out, self.config, self.array,
                                  self.times, self.focus_mm, self.codes, self.rate)

    def test_csv_has_one_row_per_frame_plus_header(self):
        lines = self.exported(".csv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(len(lines), 13)
        self.assertEqual(len(lines[0].split(",")), 5 + self.array.count)
        self.assertEqual(lines[1].split(",")[0], "0")
        self.assertEqual([int(value) for value in lines[1].split(",")[5:]],
                         [int(value) for value in self.codes[0]])

    def test_phase_mem_roundtrips_hex_codes(self):
        lines = self.exported("_phases.mem").read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 12)
        for line, row in zip(lines, self.codes):
            self.assertEqual([int(token, 16) for token in line.split()], list(row))

    def test_focus_mem_roundtrips_signed_um(self):
        lines = self.exported("_focus.mem").read_text(encoding="ascii").splitlines()
        self.assertEqual(len(lines), 12)
        for line, point in zip(lines, self.focus_mm):
            values = [int(token, 16) for token in line.split()]
            values = [value - 2 ** 32 if value >= 2 ** 31 else value for value in values]
            self.assertEqual(values, [int(round(value * 1000.0)) for value in point])

    def test_metadata_records_parameters_and_checksums(self):
        saved = json.loads(self.exported(".json").read_text(encoding="utf-8"))
        self.assertEqual(saved["frames"], 12)
        self.assertEqual(saved["array"], {"hw_rows": 4, "hw_cols": 4,
                                          "hw_pitch_um": 10000, "mapping": "ROW_MAJOR_XY"})
        self.assertEqual(saved["config"], self.config.wire())
        self.assertAlmostEqual(saved["laps"], 12 / 2000)
        for key, filename in saved["files"].items():
            digest = hashlib.sha256((self.out / filename).read_bytes()).hexdigest()
            self.assertEqual(saved["sha256"][key], digest)


if __name__ == "__main__":
    unittest.main()
