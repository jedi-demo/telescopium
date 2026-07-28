"""
SPDX-FileCopyrightText: 2024 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2

Network interface for an AIM-TTi QL355TP Series II power supply.

The device needs to be connected to the LAN with TCP port 9221 accessible.
"""

from .TTiInterface import TTiInterface
import re

class QL355TPII(TTiInterface):
    def __init__(self, ip_address: str):
        super().__init__(ip_address=ip_address)

    # Device functions

    def initialize(self):
        """ Connect to the device and clear the status. """
        self.connect()
        self._write("*CLS")

    def identify(self) -> str:
        """ Return the device identification string. """
        return self._query("*IDN?")

    def set_output(self, channel: int, enable: bool):
        """ Turn the output of the given channel on or off. """
        state = 1 if enable else 0
        self._write(f"OP{channel} {state}")

    def set_voltage(self, channel: int, voltage: float):
        """ Set the voltage of the given channel. """
        self._write(f"V{channel} {voltage}")

    def set_current_limit(self, channel: int, current: float):
        """ Set the current limit of the given channel. """
        self._write(f"I{channel} {current}")

    def set_ovp(self, channel: int, voltage: float):
        """ Set the over-voltage protection of the given channel. """
        self._write(f"OVP{channel} {voltage}")

    def set_ocp(self, channel: int, current: float):
        """ Set the over-current protection of the given channel. """
        self._write(f"OCP{channel} {current}")

    def _parse_value(self, text: str) -> float:
        """ Helper method to extract floats from SCPI responses. """
        match = re.search(r"[-+]?\d*\.\d+|\d+", text)
        if match:
            return float(match.group())
        return 0.0

    def get_voltage_readback(self, channel: int) -> float:
        """ Get the voltage readback of the given channel. """
        ret = self._query(f"V{channel}O?")
        return self._parse_value(ret)

    def get_current_readback(self, channel: int) -> float:
        """ Get the current readback of the given channel. """
        ret = self._query(f"I{channel}O?")
        return self._parse_value(ret)

    def get_limit_status(self, channel: int) -> int:
        """ Get the limit status of the given channel. """
        try:
            ret = self._query(f"LSR{channel}?")
            return int(ret)
        except:
            return 0

    def get_execution_error(self) -> int:
        """ Get the execution error. """
        try:
            ret = self._query("EER?")
            return int(ret)
        except:
            return 0
