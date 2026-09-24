"""Bounded serial I/O and demo transport; all port access belongs to one worker."""

import time

import serial
from serial.tools import list_ports

from .demo import DemoDevice


def available_ports():
    return [(port.device, port.description) for port in list_ports.comports()]


class SerialTransport:
    simulated = False

    def __init__(self, port, baudrate):
        # Avoid flow-control toggling where supported; some drivers can glitch
        # DTR/RTS on open. Firmware must always boot with output disabled.
        self.port = serial.Serial(port=None, baudrate=baudrate, timeout=0.03,
                                  write_timeout=0.25, rtscts=False, dsrdtr=False)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = port
        try:
            self.port.open()
        except Exception:
            self.port.close()
            raise

    def write(self, raw):
        if self.port.write(raw) != len(raw):
            raise IOError("串口写入不完整")

    def read(self):
        return self.port.read(min(max(self.port.in_waiting, 1), 2048))

    def close(self):
        self.port.close()


class DemoTransport:
    simulated = True

    def __init__(self, array=None):
        self.device = DemoDevice(array=array)
        self.buffer = bytearray()
        self.muted = False
        self.closed = False

    def write(self, raw):
        for response in self.device.handle(raw):
            if not self.muted:
                self.buffer.extend(response)

    def read(self):
        for frame in self.device.poll():
            if not self.muted:
                self.buffer.extend(frame)
        data = bytes(self.buffer[:173])  # deliberate fragments test real framing
        del self.buffer[:173]
        if not data:
            time.sleep(0.02)
        return data

    def button(self, action):
        raw = self.device.button(action)
        if not self.muted:
            self.buffer.extend(raw)

    def close(self):
        self.closed = True
        self.buffer.clear()
