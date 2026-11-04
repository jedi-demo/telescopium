"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Abstract serial interface for Huber temperature control units.
"""

from abc import ABCMeta, abstractmethod
from threading import Lock
from typing import NamedTuple, Optional

import serial


class HuberStatus(NamedTuple):
    """Snapshot of the unit state (one LAI "G" transaction)"""

    mode: str  # "off", "circulation" or "control"
    alarm: bool
    setpoint: float  # degC
    internal_temperature: float  # degC
    external_temperature: Optional[float]  # degC, None if no external sensor is connected


class HuberInterface(metaclass=ABCMeta):
    def __init__(
        self,
        port: str,
        baud: int = 9600,
        bits: int = serial.EIGHTBITS,
        stopbits: int = serial.STOPBITS_ONE,
        parity: str = serial.PARITY_NONE,
        timeout: float = 2.0,
    ):
        self._serial = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=bits,
            stopbits=stopbits,
            parity=parity,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            timeout=timeout,
            write_timeout=timeout,
        )
        self._lock = Lock()

    # Serial helper functions

    def _transact(self, request: str, until: bytes) -> str:
        """
        Send a request exactly as given (the caller adds the protocol terminator) and read the
        response up to and including `until`. Stale bytes in the input buffer are discarded first, so
        a previous failed or partial exchange cannot desynchronise this one.
        """
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(request.encode("ascii"))
            raw = self._serial.read_until(until)
        if not raw.endswith(until):
            raise TimeoutError(f"No complete response from Huber chiller to {request!r} (received {raw!r})")
        return raw.decode("ascii", errors="replace").strip("\r\n")

    # Device functions

    @abstractmethod
    def reset(self) -> None:
        """Bring the unit to a safe idle state"""
        pass

    @abstractmethod
    def identify(self) -> str:
        """Identify device"""
        pass

    @abstractmethod
    def read_status(self, max_age: float = 1.0) -> HuberStatus:
        """
        Read mode, alarm, setpoint, internal and external temperature in a single transaction.
        A cached reading younger than `max_age` seconds may be returned; use 0 to force a new reading.
        """
        pass

    @abstractmethod
    def set_setpoint(self, temperature: float) -> None:
        """Set temperature setpoint in degC (validated against the unit limits)"""
        pass

    @abstractmethod
    def enable_control(self, enable: bool) -> None:
        """Start or stop the temperature control process (including circulation)"""
        pass

    @abstractmethod
    def acknowledge_alarm(self) -> None:
        """Silence a pending alarm tone"""
        pass

    @abstractmethod
    def get_temperature_limits(self) -> dict[str, float]:
        """Setpoint limits and working range of the unit"""
        pass

    # Device helper functions

    @abstractmethod
    def initialize(self) -> None:
        """Check communication with the unit"""
        pass

    @abstractmethod
    def release(self) -> None:
        """Release the serial port"""
        pass

    # Derived accessors

    def get_setpoint(self) -> float:
        return self.read_status().setpoint

    def get_internal_temperature(self) -> float:
        return self.read_status().internal_temperature

    def get_external_temperature(self) -> Optional[float]:
        return self.read_status().external_temperature

    def get_mode(self) -> str:
        return self.read_status().mode

    def is_control_active(self) -> bool:
        return self.read_status().mode == "control"

    def is_circulation_running(self) -> bool:
        return self.read_status().mode in ("circulation", "control")

    def get_alarm_status(self) -> bool:
        return self.read_status().alarm

    def check_setpoint(self, temperature: float) -> None:
        """Raise ValueError if the setpoint is outside the limits of the unit"""
        limits = self.get_temperature_limits()
        low, high = limits["setpoint_min"], limits["setpoint_max"]
        if not low <= temperature <= high:
            raise ValueError(f"Setpoint {temperature} degC outside the allowed range [{low}, {high}] degC")
