from dataclasses import replace
import csv
import json
import io
from pathlib import Path
import tempfile
import unittest

from desktop_app.measurement_frame import TableFrame, placement_record
from desktop_app.field_scan import ScanBook
from desktop_app.receiver import Capture
from desktop_app.tests import test_field_compare


class MeasurementFrameTests(unittest.TestCase):
    def test_real_corner_and_orientation(self):
        frame = TableFrame()
        self.assertEqual(frame.to_array((132, 94, 24.7)), (0., 0., 100.))
        self.assertEqual(frame.to_array((142, 114, 34.7)), (10., -20., 90.))
        for position in ((0., 0., 0.), (132., 94., 24.7), (143.2, 80., 29.7)):
            for a, b in zip(position, frame.to_table(frame.to_array(position))):
                self.assertAlmostEqual(a, b)
        self.assertEqual(TableFrame.parse(frame.document()), frame)
        self.assertEqual(placement_record(frame, (98.727, 73.872, 24.7), "TRAY_A")["receiver_table_mm"],
                         [132., 94., 24.7])

    def test_capture_export_both_frames_and_legacy_import(self):
        fixture = test_field_compare.FieldComparisonTests()
        fixture.setUp()
        book = fixture.book()
        point = book.points[0]
        placement = placement_record(TableFrame(), (98.727, 73.872, 24.7), "TRAY_A")
        # Also exercise actual MG24 full scale/rate, without real hardware.
        mg24 = replace(point.capture, pin="MG24_D0", fs_hz=397959, full_scale_mv=1210)
        new = ScanBook()
        new.add_windows(fixture.state, [mg24], (0., 0., 100.), placement=placement)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"field.csv"
            new.save_csv(path)
            imported = ScanBook.load_csv(path)
            self.assertEqual(imported.points, new.points)
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
            self.assertEqual(float(rows[0]["table_x_mm"]), 132.)
            rows[0]["placement_json"] = json.dumps({**placement, "receiver_table_mm": [0, 0, 24.7]})
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ValueError):
                ScanBook.load_csv(path)
            book.save_csv(path)
            rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
            for row in rows:
                row["schema"] = "touchsee-field-scan-1"
                for key in ("placement_json", "table_x_mm", "table_y_mm", "table_z_mm"):
                    del row[key]
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            self.assertEqual(ScanBook.load_csv(path).points, book.points)


if __name__ == "__main__":
    unittest.main()
