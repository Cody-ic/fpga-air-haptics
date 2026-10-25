import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibrate_fpga as cal


class Connection:
    def __init__(self):
        self.chunks = asyncio.Queue()

    async def read(self):
        return await self.chunks.get()

    async def close(self):
        pass


class StatusBurstTest(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_burst_preserves_replies_and_new_measurements(self):
        connection = Connection()
        link = cal.FpgaLink(connection)
        status = cal.encode(cal.STATUS, 123, 0, 0, 4, 0x1e0808, 70000, 10300)
        ack = cal.encode(cal.SETUP_ACK, 123, 0, 0, 0, 70000, 30, 0)
        changed = cal.encode(cal.STATUS, 123, 1, 2, 5, 0x1e0808, 70000, 10300)
        try:
            # Model an MG24 capture delaying consumption while the FPGA floods
            # identical idle statuses, followed by an ACK and new measurement.
            await connection.chunks.put(status * 150 + ack + changed * 150)
            await asyncio.sleep(.01)
            self.assertIsNone(link.error)
            packets = [await link.receive() for _ in range(3)]
            self.assertEqual([(p.kind, p.token) for p in packets],
                             [(cal.STATUS, 0), (cal.SETUP_ACK, 0), (cal.STATUS, 1)])
            # Once consumed, the next identical status must still be delivered.
            await connection.chunks.put(changed)
            self.assertEqual((await link.receive()).token, 1)
        finally:
            await link.close()


if __name__ == '__main__':
    unittest.main()
