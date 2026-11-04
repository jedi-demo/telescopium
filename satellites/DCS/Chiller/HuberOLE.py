"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Serial interface for Huber Minichiller / Unichiller OLE (LAI and PP protocols).

The unit uses a fixed 8N1 serial format without handshake. Only the baud rate (9600 - 115200) can be
configured on the unit and has to match the `baudrate` used here.

Protocol differences (see chapter 6.1 of the operation manual):
- LAI commands ("[M01...") are terminated by CR in both directions and carry a length field and a checksum.
- PP commands ("SP?", "CA@ 00001", ...) are terminated by CR+LF in both directions and have no checksum.
"""

import time
from threading import Lock
from typing import Optional

import serial

from .HuberInterface import HuberInterface, HuberStatus

# Without an external sensor the unit reports -151.00 degC
_NO_SENSOR_BELOW = -150.0

# LAI "G" temperature control mode characters
_MODES = {"O": "off", "C": "circulation", "I": "control"}


class HuberOLE(HuberInterface):
    def __init__(self, port: str, baudrate: int = 9600):
        super().__init__(
            port=port,
            baud=baudrate,
            bits=serial.EIGHTBITS,
            stopbits=serial.STOPBITS_ONE,
            parity=serial.PARITY_NONE,
            timeout=2.0,
        )
        self._status_lock = Lock()
        self._status: Optional[HuberStatus] = None
        self._status_time = 0.0

    # LAI helpers

    @staticmethod
    def _checksum(msg: str) -> str:
        """1 byte sum of all characters (including the start character), as two hex characters"""
        return f"{sum(ord(c) for c in msg) & 0xFF:02X}"

    @staticmethod
    def _hex_to_temp(hex_str: str) -> float:
        """Signed 16-bit value in 0.01 degC steps (4 hex characters) to degC"""
        value = int(hex_str, 16)
        if value > 0x7FFF:
            value -= 0x10000
        return value / 100.0

    def _lai(self, qualifier: str, payload: str = "") -> str:
        """Send an LAI command and return the validated response (without the CR)"""
        # Length field: all bytes without checksum and end character
        length = 7 + len(payload)
        base = f"[M01{qualifier}{length:02X}{payload}"
        res = self._transact(f"{base}{self._checksum(base)}\r", until=b"\r")

        if not res.startswith(f"[S01{qualifier}") or len(res) < 9:
            raise RuntimeError(f"Invalid LAI response: {res!r}")
        try:
            declared_length = int(res[5:7], 16)
        except ValueError:
            raise RuntimeError(f"Invalid length field in LAI response: {res!r}") from None
        if declared_length != len(res) - 2:
            raise RuntimeError(f"LAI response length mismatch (declared {declared_length}, got {len(res) - 2}): {res!r}")
        if res[-2:].upper() != self._checksum(res[:-2]):
            raise RuntimeError(f"LAI response checksum mismatch: {res!r}")
        return res

    # PP helper

    def _pp(self, command: str, prefix: str) -> int:
        """Send a PP command and return the numeric value of the response (e.g. 'SP +02500' -> 2500)"""
        res = self._transact(f"{command}\r\n", until=b"\n")
        parts = res.split()
        if len(parts) != 2 or parts[0] != prefix:
            raise RuntimeError(f"Invalid PP response to {command!r}: {res!r}")
        try:
            return int(parts[1])
        except ValueError:
            raise RuntimeError(f"Invalid PP value in response to {command!r}: {res!r}") from None

    def _invalidate_status(self) -> None:
        with self._status_lock:
            self._status = None

    # Device functions

    def reset(self) -> None:
        self.enable_control(False)

    def identify(self) -> str:
        res = self._lai("V")
        return f"Huber OLE ID: {res[7:-2]}"

    def read_status(self, max_age: float = 1.0) -> HuberStatus:
        with self._status_lock:
            if self._status is not None and max_age > 0 and time.monotonic() - self._status_time <= max_age:
                return self._status

            # All "*": do not change mode, alarm or setpoint, just query
            res = self._lai("G", "******")
            if len(res) != 23:
                raise RuntimeError(f"Invalid LAI 'G' response length: {res!r}")
            mode, alarm = res[7], res[8]
            if mode not in _MODES or alarm not in "01":
                raise RuntimeError(f"Invalid mode/alarm in LAI 'G' response: {res!r}")
            try:
                setpoint = self._hex_to_temp(res[9:13])
                internal = self._hex_to_temp(res[13:17])
                external: Optional[float] = self._hex_to_temp(res[17:21])
            except ValueError:
                raise RuntimeError(f"Invalid temperature field in LAI 'G' response: {res!r}") from None
            if external is not None and external < _NO_SENSOR_BELOW:
                external = None

            self._status = HuberStatus(_MODES[mode], alarm == "1", setpoint, internal, external)
            self._status_time = time.monotonic()
            return self._status

    def set_setpoint(self, temperature: float) -> None:
        self.check_setpoint(temperature)
        hundredths = round(temperature * 100)
        value = self._pp(f"SP@ {hundredths:+06d}", "SP")
        self._invalidate_status()
        if value != hundredths:
            raise RuntimeError(f"Setpoint set to {hundredths / 100} degC but {value / 100} degC was applied")

    def enable_control(self, enable: bool) -> None:
        target = 1 if enable else 0
        value = self._pp(f"CA@ {target:05d}", "CA")
        self._invalidate_status()
        if value != target:
            raise RuntimeError(f"Temperature control {'start' if enable else 'stop'} not confirmed (response: {value})")

    def acknowledge_alarm(self) -> None:
        # Byte 9 = "1": acknowledge pending alarm tone; mode and setpoint unchanged
        self._lai("G", "*1****")
        self._invalidate_status()

    def get_temperature_limits(self) -> dict[str, float]:
        res = self._lai("L", "********")
        raw = res[7:-2]
        if len(raw) != 16:
            raise RuntimeError(f"Invalid LAI 'L' response: {res!r}")
        try:
            return {
                "setpoint_min": self._hex_to_temp(raw[0:4]),
                "setpoint_max": self._hex_to_temp(raw[4:8]),
                "working_range_min": self._hex_to_temp(raw[8:12]),
                "working_range_max": self._hex_to_temp(raw[12:16]),
            }
        except ValueError:
            raise RuntimeError(f"Invalid limit values in LAI 'L' response: {res!r}") from None

    # Device helper functions

    def initialize(self) -> None:
        # Raises if the unit does not answer or answers with an invalid frame
        self.identify()

    def release(self) -> None:
        if self._serial.is_open:
            self._serial.close()
