import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from calibrate_fpga import ABORT, MEASURE, READY, SETUP, SETUP_ACK, STATUS, Packet
from screen_fpga_channels import Settings, screen_one, summarize
from receiver_client import ProtocolError


class Link:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.commands = []
        self.token = 100
        self.target = 0
        self.stage = self.repeat = 0
        self.bad_stage = False

    def status(self, state, output=False):
        flags = 4 | 16 | (1 if state in (1, 2) else 0) | (64 if output else 0)
        return Packet(STATUS, 7, self.token, (state | self.stage << 8, flags,
                      self.target << 24 | 30 << 16 | 8 << 8 | 8, 70000, 10300))

    async def receive(self):
        return await self.queue.get()

    async def request_sample(self):
        self.token += 1
        await self.queue.put(self.status(2, self.stage != 0))

    async def send(self, kind, boot, token=0, *args):
        self.commands.append((kind, token, args))
        if kind == SETUP:
            self.target = args[-1]
            self.stage = self.repeat = 0
            await self.queue.put(Packet(SETUP_ACK, 7, self.token, args))
            await self.queue.put(self.status(5))
        elif kind == READY:
            await self.queue.put(self.status(1))
            if self.bad_stage:
                self.stage = 2
            await self.request_sample()
        elif kind == MEASURE:
            self.repeat += 1
            if self.repeat == 3:
                self.stage += 1
                self.repeat = 0
            packet = self.status(2, self.stage != 0)
            self.token += 1
            # repeat occupies flags bits 8..9 in the real endpoint.
            await self.queue.put(Packet(packet.kind, 7, self.token,
                (packet.args[0], packet.args[1] | self.repeat << 8, *packet.args[2:])))
        elif kind == ABORT:
            await self.queue.put(self.status(5))


class Receiver:
    def __init__(self, amplitude, clipped=False):
        self.calls = 0
        self.amplitude = amplitude
        self.clipped = clipped

    async def capture(self, raw=False):
        self.calls += 1
        amplitude = .1 if self.calls <= 3 else self.amplitude
        clipped = self.clipped and self.calls > 3
        return dict(peak40_adc=amplitude, peak40_mv=amplitude * 3300 / 4095,
                    valid=not clipped, adc_rail=clipped)


class ScreeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.beat = asyncio.create_task(asyncio.sleep(60))

    async def asyncTearDown(self):
        self.beat.cancel()
        try:
            await self.beat
        except asyncio.CancelledError:
            pass

    async def test_weak_channel_does_not_block_next_and_never_advances_past_reference(self):
        link = Link()
        rows = []
        results = []
        for target, amplitude in ((0, 100), (32, .1), (33, 120)):
            results.append(await screen_one(link, Receiver(amplitude), 7, target,
                                           Settings(), rows.append, self.beat))
        self.assertEqual([r['screen_pass'] for r in results], [True, False, True])
        self.assertEqual(results[1]['issues'], ['weak_signal'])
        commands = [c[0] for c in link.commands]
        self.assertEqual(commands.count(ABORT), 3)
        self.assertEqual(commands.count(MEASURE), 15)
        self.assertEqual(set(commands), {SETUP, READY, MEASURE, ABORT})
        self.assertFalse(any((c[2][3] >> 8) == 1 and c[2][4] == 2
                             for c in link.commands if c[0] == MEASURE))
        self.assertTrue(all(not r['phase_verified'] for r in results))

    async def test_clipped_capture_stops_channel_without_submitting_invalid_power(self):
        link = Link()
        result = await screen_one(link, Receiver(4095, clipped=True), 7, 32,
                                  Settings(), lambda _: None, self.beat)
        self.assertIn('invalid_signal', result['issues'])
        self.assertEqual(sum(c[0] == MEASURE for c in link.commands), 3)
        self.assertEqual(link.commands[-1][0], ABORT)

    async def test_unexpected_phase_stage_aborts(self):
        link = Link()
        link.bad_stage = True
        with self.assertRaises(ProtocolError):
            await screen_one(link, Receiver(100), 7, 32, Settings(), lambda _: None, self.beat)
        self.assertEqual(link.commands[-1][0], ABORT)

    def test_power_domain_spread_threshold(self):
        samples = [dict(stage=s, power_adc2=p, good=True, capture=dict(peak40_mv=1))
                   for s, values in ((0, [0, 0, 0]), (1, [100, 100, 116])) for p in values]
        self.assertIn('unstable_signal', summarize(samples)['issues'])


if __name__ == '__main__':
    unittest.main()
