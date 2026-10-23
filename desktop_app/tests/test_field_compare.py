from dataclasses import replace
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from desktop_app.acoustics import pressure_at
from desktop_app.demo import DemoDevice
from desktop_app.field_compare import ScanModel, compare_scan
from desktop_app.field_scan import ScanBook
from desktop_app.model import Config
from desktop_app.protocol import Snapshot, decode
from desktop_app.receiver import Capture


class FieldComparisonTests(unittest.TestCase):
    def setUp(self):
        device = DemoDevice()
        device.config = Config(shape="POINT", z_um=100000, level=100, mod_hz=0)
        device.state = "RUNNING"
        self.state = Snapshot.parse(decode(device.snapshot()).fields)
        self.model = ScanModel().document()
        self.coordinates = ((0., 0., 100.), (15., 0., 100.), (30., 0., 100.), (0., 15., 100.))

    def book(self, gain=1.):
        book = ScanBook()
        amplitudes = abs(pressure_at(self.state.array, self.state.config, self.state.phases,
                                     self.coordinates))
        for i, (position, amplitude) in enumerate(zip(self.coordinates, amplitudes)):
            windows = []
            for variation in (.98, 1., 1.02):
                peak = 900*gain*variation*amplitude/amplitudes[0]
                raw = tuple(round(2000+peak*math.sin(2*math.pi*n/10)) for n in range(200))
                windows.append(Capture(self.state.boot, self.state.revision, self.state.uptime_ms+i+1,
                    True, True, "PA0", 400000, 12, raw))
            book.add_windows(self.state, windows, position, prediction_model=self.model)
        return book

    def test_same_coordinates_reference_and_unknown_gain(self):
        a, b = self.book(), self.book(.6)
        result = compare_scan(a.context, a.points, a.prediction_model, 100)
        reduced = compare_scan(b.context, b.points, b.prediction_model, 100)
        np.testing.assert_equal(result.positions, self.coordinates)
        np.testing.assert_allclose(result.measured_ratio, result.predicted_ratio, atol=.001)
        # Smaller signals amplify the 12-bit rounding error in both numerator
        # and reference denominator; allow 0.3% of the reference amplitude.
        np.testing.assert_allclose(reduced.measured_ratio, result.measured_ratio, atol=.003)
        self.assertLess(result.rmse, .001)
        self.assertEqual(result.reference, self.coordinates[0])
        self.assertEqual(result.measured_ratio[0], 1)
        self.assertEqual(result.predicted_ratio[0], 1)
        np.testing.assert_equal(result.counts, [3]*4)
        np.testing.assert_equal(result.profile_indices, [0, 1, 2])
        self.assertTrue(np.all(result.minimum_mv < result.measured_mv))
        self.assertTrue(np.all(result.maximum_mv > result.measured_mv))

    def test_reference_excluded_from_error_and_only_one_point_not_validated(self):
        book = self.book()
        result = compare_scan(book.context, book.points[:3], self.model, 100)
        self.assertIsNone(result.rmse)
        altered = replace(book.points[3], capture=replace(book.points[3].capture,
                          raw=tuple(2000+round((v-2000)*1.3) for v in book.points[3].capture.raw)))
        points = book.points[:3]+[altered]*3
        result = compare_scan(book.context, points, self.model, 100)
        self.assertAlmostEqual(result.rmse, abs(result.measured_ratio[1]-result.predicted_ratio[1]))
        self.assertGreater(result.rmse, .05)

    def test_reference_switch_section_selection_and_phase_error(self):
        book = self.book()
        result = compare_scan(book.context, book.points, self.model, 100,
                              reference=self.coordinates[1], direction="y", section_mm=0)
        self.assertEqual(result.reference, self.coordinates[1])
        self.assertEqual(result.measured_ratio[1], 1)
        np.testing.assert_equal(result.profile_indices, [0, 3])
        wrong = compare_scan(book.context, book.points, ScanModel(10, -1).document(), 100)
        self.assertGreater(wrong.rmse, .02)

    def test_no_data_unknown_reference_and_zero_signal_rejected(self):
        book = self.book()
        for z, reference in ((200, None), (100, (999, 0, 100))):
            with self.assertRaises(ValueError):
                compare_scan(book.context, book.points, self.model, z, reference)
        zero = replace(book.points[0], capture=replace(book.points[0].capture, raw=(2000,)*200))
        with self.assertRaises(ValueError):
            compare_scan(book.context, [zero], self.model, 100)
        with self.assertRaises(ValueError):
            ScanModel(10, 0).document()
        with self.assertRaises(ValueError):
            ScanModel(10, 1, "unknown-model").document()
        for incomplete in (None, {}, {"phase_direction": -1}):
            with self.assertRaises(ValueError):
                ScanModel.parse(incomplete)
        with self.assertRaises(ValueError):
            ScanModel(True, 1).document()

    def test_recorded_model_atomic_roundtrip_legacy_and_paired_export(self):
        book = self.book()
        with self.assertRaises(ValueError):
            book.add_windows(self.state, [book.points[0].capture], (2, 0, 100),
                             prediction_model=ScanModel(0, -1).document())
        self.assertEqual(len(book.points), 12)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"scan.csv"
            book.save_csv(path)
            loaded = ScanBook.load_csv(path)
            self.assertEqual(loaded.prediction_model, book.prediction_model)
            self.assertEqual(loaded.points, book.points)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            fields = [k for k in rows[0] if k != "prediction_model_json"]
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            self.assertIsNone(ScanBook.load_csv(path).prediction_model)
            result = compare_scan(book.context, book.points, self.model, 100)
            result.save_csv(path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                pairs = list(csv.DictReader(stream))
        self.assertEqual(len(pairs), 4)
        self.assertEqual(pairs[0]["source"], "DEMO_SYNTHETIC")
        self.assertEqual(pairs[0]["is_reference"], "1")
        self.assertEqual(float(pairs[1]["measured_peak40_mv"]), result.measured_mv[1])
        self.assertEqual(float(pairs[1]["predicted_ratio"]), result.predicted_ratio[1])
        metadata = json.loads(pairs[0]["metadata_json"])
        self.assertEqual(metadata["prediction_model"], self.model)
        self.assertEqual(metadata["context"], book.context)
        self.assertEqual(metadata["reference_mm"], list(self.coordinates[0]))


if __name__ == "__main__":
    unittest.main()
