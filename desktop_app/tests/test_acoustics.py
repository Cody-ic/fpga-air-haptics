from dataclasses import replace
import csv
import math
from pathlib import Path
import tempfile
import unittest
import zipfile
import json
from types import SimpleNamespace

import numpy as np

from desktop_app.acoustics import FieldSettings, compute_field, piston_directivity, pressure_at
from desktop_app.array_geometry import grid_geometry
from desktop_app.demo import DemoDevice
from desktop_app.field_scan import ScanBook
from desktop_app.model import ArraySpec, Config, focus_phases, field_slice
from desktop_app.protocol import decode, Snapshot
from desktop_app.receiver import AdcProbe, Capture


class AcousticModelTests(unittest.TestCase):
    def setUp(self):
        self.array = ArraySpec.from_geometry(grid_geometry(4, 4, 11))
        self.config = Config(shape="POINT", z_um=100000, level=100, mod_hz=0)
        self.focus = (0., 0., 100.)
        self.phases = focus_phases(self.config, self.focus, self.array)

    def test_phase_alignment_and_cancellation_are_visible(self):
        coherent = abs(pressure_at(self.array, self.config, self.phases, self.focus))
        cancelled = self.phases.copy()
        # Mirror pairs have the same transfer magnitude at the centre.
        cancelled[:8] = (cancelled[:8]+32) % 64
        self.assertGreater(coherent, 14)
        self.assertLess(abs(pressure_at(self.array, self.config, cancelled, self.focus)), 1e-10)

    def test_counter_waveform_fourier_phase_and_propagation(self):
        # Independently obtain emitted phase from the existing MCU's 64-slot
        # (slot+code)<32 pulse, then propagate with physical time delay -k*r.
        slots = np.arange(64)
        base = np.fft.rfft((slots < 32).astype(float))[1]
        emitted = np.array([np.fft.rfft(((slots+int(code)) % 64 < 32).astype(float))[1]/base
                            for code in self.phases])
        distances = np.linalg.norm(np.asarray(self.array.geometry.positions_mm)-self.focus, axis=1)
        k = 2*np.pi*self.config.carrier_hz/self.array.geometry.sound_speed_mm_s
        expected = abs(np.sum(100/distances*emitted*np.exp(-1j*k*distances)))
        predicted = abs(pressure_at(self.array, self.config, self.phases, self.focus,
                                   diameter_mm=0, phase_direction=-1))
        self.assertAlmostEqual(predicted, expected, places=10)
        corrected_codes = (-self.phases) % 64
        corrected = abs(pressure_at(self.array, self.config, corrected_codes, self.focus,
                                   diameter_mm=0, phase_direction=-1))
        self.assertGreater(corrected, predicted+2)

    def test_gui_phase_encoding_identifies_known_mcus_and_rejects_unknown(self):
        from desktop_app.acoustic_panel import AcousticPanel
        def value(text):
            return SimpleNamespace(get=lambda: text)
        session = SimpleNamespace(is_demo=False, hello_fields={"device": "STM32F103C8T6"})
        panel = SimpleNamespace(app=SimpleNamespace(ready=True, session=session),
            phase_encoding=value("自动识别"), source=value("设备回读"), plane=value("XY"),
            diameter=value("10"), span=value("60"), depth=value("220"))
        self.assertEqual(AcousticPanel.settings(panel).phase_direction, -1)
        panel.source = value("待发图形")
        self.assertEqual(AcousticPanel.settings(panel).phase_direction, 1)
        self.assertEqual(AcousticPanel.settings(panel, measurement=True).phase_direction, -1)
        panel.source = value("设备回读")
        session.hello_fields = {"device": "UNVERIFIED-FPGA"}
        with self.assertRaises(ValueError):
            AcousticPanel.settings(panel)
        panel.phase_encoding = value("相位码表示延迟")
        self.assertEqual(AcousticPanel.settings(panel).phase_direction, 1)

    def test_single_channel_scaling_and_fixed_reference(self):
        one = ArraySpec(1, 1)
        config = self.config
        self.assertAlmostEqual(abs(pressure_at(one, config, [0], (0, 0, 100))), 1)
        self.assertAlmostEqual(abs(pressure_at(one, config, [0], (0, 0, 200))), .5)
        half = abs(pressure_at(self.array, config, self.phases, self.focus, channel_mask=255))
        full = abs(pressure_at(self.array, config, self.phases, self.focus))
        self.assertAlmostEqual(full, half*2)
        self.assertEqual(pressure_at(self.array, config, self.phases, self.focus, 0), 0)

    def test_piston_uses_orientation_and_signed_transfer(self):
        np.testing.assert_allclose(piston_directivity([1, -1, 0], 4), [1, 0, 0])
        self.assertLess(float(piston_directivity([.2], 5)[0]), 0)
        g = grid_geometry(1, 1)
        rotated = replace(g, elements=((0, 0, 0, 1000000, 0, 0),))
        a = ArraySpec.from_geometry(rotated)
        self.assertAlmostEqual(abs(pressure_at(a, self.config, [0], (100, 0, 0))), 1)
        self.assertEqual(pressure_at(a, self.config, [0], (-100, 0, 0)), 0)

    def test_half_width_and_output_off(self):
        result = compute_field(self.array, self.config, self.phases, self.focus)
        self.assertLessEqual(abs(result.peak_position_mm[0]), 1.2)
        self.assertLessEqual(abs(result.peak_position_mm[1]), 1.2)
        self.assertGreater(result.width_mm, 5)
        self.assertFalse(result.clipped)
        off = compute_field(self.array, self.config, self.phases, self.focus, enabled=False)
        self.assertTrue(np.all(off.amplitude == 0))
        self.assertIsNone(off.width_mm)
        self.assertEqual(off.target_amplitude, 0)
        self.assertTrue(np.isfinite(off.values("相对 dB")[0]).all())

    def test_f103_density_level_does_not_fake_voltage_reduction(self):
        reduced = replace(self.config, level=30, mod_hz=200)
        a = compute_field(self.array, self.config, self.phases, self.focus)
        b = compute_field(self.array, reduced, self.phases, self.focus)
        np.testing.assert_allclose(a.amplitude, b.amplitude)
        np.testing.assert_allclose(field_slice(self.config, self.phases, self.focus, array=self.array)[2],
                                   field_slice(reduced, self.phases, self.focus, array=self.array)[2])

    def test_r5_pcb_wiring_matches_logical_geometry(self):
        root = Path(__file__).resolve().parents[2]
        package = root/"hardware/Haptics_4x4_R5_12VDC"
        with zipfile.ZipFile(package/"Haptics_4x4_R5_12VDC.epro") as archive:
            name = next(n for n in archive.namelist() if n.endswith(".epcb"))
            records = [json.loads(line) for line in archive.read(name).decode("utf-8").splitlines() if line.strip()]
        refs = {x[3]: x[8] for x in records if x[0] == "ATTR" and x[7] == "Designator"}
        with (package/"Positions.csv").open(encoding="utf-16", newline="") as stream:
            positions = {r["Designator"]: (float(r["Mid X"][:-2])-27.5, float(r["Mid Y"][:-2])+27.5)
                         for r in csv.DictReader(stream, delimiter="\t") if "MA40" in r["Device"]}
        outputs = {x[3]: positions[refs[x[1]]] for x in records if x[0] == "PAD_NET"
                   and x[1] in refs and refs[x[1]] in positions and x[3].startswith("OUT")}
        wired_outputs = [4, 5, 6, 7, 0, 1, 2, 3, 12, 13, 14, 15, 8, 9, 10, 11]
        for channel, pcb_output in enumerate(wired_outputs):
            np.testing.assert_allclose(outputs[f"OUT{pcb_output}"], self.array.geometry.positions_mm[channel][:2])

    def test_invalid_mask_phase_and_model_domain(self):
        for mask in (-1, 1 << 16, True):
            with self.assertRaises(ValueError):
                pressure_at(self.array, self.config, self.phases, self.focus, mask)
        with self.assertRaises(ValueError):
            pressure_at(self.array, self.config, [64]*16, self.focus)
        with self.assertRaises(ValueError):
            pressure_at(ArraySpec(1, 1), self.config, [0], (0, 0, 0))
        with self.assertRaises(ValueError):
            FieldSettings(diameter_mm=float("nan")).validate()
        with self.assertRaises(ValueError):
            FieldSettings(phase_direction=0).validate()


class ScanTests(unittest.TestCase):
    def setUp(self):
        device = DemoDevice()
        device.config = Config(shape="POINT", level=100, mod_hz=0)
        device.state = "RUNNING"
        self.state = Snapshot.parse(decode(device.snapshot()).fields)
        self.capture = Capture(self.state.boot, self.state.revision, self.state.uptime_ms+10,
                               True, True, "PA0", 400000, 12,
                               tuple(round(1000+100*math.sin(2*math.pi*i/10)) for i in range(200)),
                               probe=AdcProbe(True, 8, 900, 1100, 1000, 1500))

    def test_atomic_raw_windows_roundtrip_and_median(self):
        book = ScanBook()
        book.add_windows(self.state, [self.capture]*3, (2, 4, 100))
        self.assertEqual(len(book.plane_points(100)), 1)
        self.assertEqual(book.plane_points(100)[0][3], 3)
        self.assertFalse(book.plane_points(150))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"scan.csv"
            book.save_csv(path)
            loaded = ScanBook.load_csv(path)
        self.assertEqual(loaded.context, book.context)
        self.assertEqual(loaded.points, book.points)
        self.assertTrue(loaded.imported)

    def test_rejects_mixed_boot_revision_source_and_clipping_atomically(self):
        for bad in (replace(self.capture, boot="other"), replace(self.capture, revision=99),
                    replace(self.capture, simulated=False), replace(self.capture, tx_running=False),
                    replace(self.capture, raw=(4095,)*200)):
            book = ScanBook()
            with self.assertRaises(ValueError):
                book.add_windows(self.state, [self.capture, bad], (0, 0, 100))
            self.assertIsNone(book.context)
            self.assertFalse(book.points)
        book = ScanBook()
        book.add_windows(self.state, [self.capture], (0, 0, 100))
        with self.assertRaises(ValueError):
            book.add_windows(replace(self.state, revision=1), [self.capture], (2, 0, 100))

    def test_gate_modulation_moving_focus_and_level(self):
        for state in (replace(self.state, output=False),
                      replace(self.state, config=replace(self.state.config, mod_hz=200)),
                      replace(self.state, config=replace(self.state.config, level=30)),
                      replace(self.state, config=replace(self.state.config, shape="CIRCLE"))):
            with self.assertRaises(ValueError):
                ScanBook.context_for(state)

    def test_tampered_units_analysis_and_source_rejected(self):
        book = ScanBook()
        book.add_windows(self.state, [self.capture], (0, 0, 100))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"scan.csv"
            book.save_csv(path)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            for field, value in (("source", "ADC_UNCALIBRATED"), ("adc_peak40_mv", "999"), ("z_mm", "nan")):
                altered = dict(rows[0], **{field: value})
                with path.open("w", encoding="utf-8-sig", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=altered.keys())
                    writer.writeheader()
                    writer.writerow(altered)
                with self.assertRaises(ValueError):
                    ScanBook.load_csv(path)


if __name__ == "__main__":
    unittest.main()
