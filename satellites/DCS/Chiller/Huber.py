"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Provides the class for the Huber chiller satellite
"""

from typing import Any

from constellation.core.commandmanager import cscp_requestable
from constellation.core.configuration import Configuration
from constellation.core.monitoring import schedule_metric
from constellation.core.protocol.cscp1 import SatelliteState, states_except
from constellation.core.satellite import Satellite
import time
from .HuberInterface import HuberInterface
from .HuberOLE import HuberOLE

_SUPPORTED_DEVICES = {
    "MINICHILLER_300": HuberOLE,
}


class Chiller(Satellite):
    device: HuberInterface

    # Defaults, so that fail_gracefully() and reentry() are safe even if initializing failed early
    target_setpoint: float = 20.0
    stop_on_landing: bool = False

    def do_initializing(self, config: Configuration) -> None:
        # Release the device of a previous initialization, if any
        if hasattr(self, "device"):
            self.device.release()
            del self.device

        device_name = config.get_str("device", default_value="MINICHILLER_300")
        if device_name not in _SUPPORTED_DEVICES.keys():
            raise ValueError(f"Device {device_name} not supported")

        port = config.get_str("port")
        baudrate = config.get_int("baudrate", default_value=9600)
        self.target_setpoint = config.get_float("setpoint", default_value=20.0)
        self.stop_on_landing = config.get_bool("stop_on_landing", default_value=False)

        self.log.info(f"Initializing Huber {device_name}")
        self.device = _SUPPORTED_DEVICES[device_name](port, baudrate)

        try:
            self.device.initialize()
            self.log.info("Device: %s", self.device.identify())
            self.device.check_setpoint(self.target_setpoint)
        except Exception:
            # Do not leave the serial port open if the unit does not answer or the setpoint is invalid
            self.device.release()
            del self.device
            raise

    def do_launching(self) -> str:
        self.log.info(f"Setting setpoint to {self.target_setpoint} degC")
        self.device.set_setpoint(self.target_setpoint)

        self.log.info("Starting temperature control")
        self.device.enable_control(True)

        status = self.device.read_status(max_age=0)
        return f"Huber running. Temperature: {status.internal_temperature:.2f} degC, setpoint: {status.setpoint:.2f} degC"

    def do_landing(self) -> str:
        if self.stop_on_landing:
            self.log.info("Stopping temperature control")
            self.device.enable_control(False)
        else:
            self.log.info("Leaving temperature control running (stop_on_landing is false)")

        status = self.device.read_status(max_age=0)
        return f"Huber mode: {status.mode}. Temperature: {status.internal_temperature:.2f} degC"

    def do_reconfigure(self, partial_config: Configuration) -> str:
        config_keys = partial_config.get_keys()

        for key in ["device", "port", "baudrate"]:
            if key in config_keys:
                raise ValueError(f"Reconfiguring {key} is not possible")

        if "stop_on_landing" in config_keys:
            self.stop_on_landing = partial_config.get_bool("stop_on_landing")

        if "setpoint" in config_keys:
            new_setpoint = partial_config.get_float("setpoint")
            self.log.info(f"Changing setpoint from {self.target_setpoint} degC to {new_setpoint} degC")
            self.device.set_setpoint(new_setpoint)
            self.target_setpoint = new_setpoint

        return f"Huber setpoint: {self.target_setpoint:.2f} degC"

    def fail_gracefully(self) -> None:
        if hasattr(self, "device") and self.stop_on_landing:
            self.log.info("Attempting to stop temperature control after failure")
            try:
                self.device.enable_control(False)
            except Exception as e:
                self.log.warning("Could not stop temperature control: %s", e)

    def reentry(self) -> None:
        if hasattr(self, "device"):
            if self.stop_on_landing:
                try:
                    self.device.enable_control(False)
                except Exception as e:
                    self.log.warning("Could not stop temperature control: %s", e)
            self.device.release()
        super().reentry()

    # Commands

    @cscp_requestable(
        states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring, SatelliteState.ERROR])
    )
    def identify(self) -> tuple[str, Any, dict[str, Any]]:
        """Identify device"""
        return self.device.identify(), None, {}

    @cscp_requestable(
        states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring, SatelliteState.ERROR])
    )
    def acknowledge_alarm(self) -> tuple[str, Any, dict[str, Any]]:
        """Acknowledge an active audible alarm"""
        self.device.acknowledge_alarm()
        return "Alarm acknowledged", None, {}

    @cscp_requestable(
        states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring, SatelliteState.ERROR])
    )
    def get_limits(self) -> tuple[str, Any, dict[str, Any]]:
        """Read setpoint limits and working range"""
        limits = self.device.get_temperature_limits()
        return f"Temperature limits: {limits}", limits, {}

    @cscp_requestable(
        states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring, SatelliteState.ERROR])
    )
    def get_chiller_status(self) -> tuple[str, Any, dict[str, Any]]:
        """Read temperatures, setpoint, mode and alarm state"""
        s = self.device.read_status(max_age=0)
        status = {
            "internal_temperature": s.internal_temperature,
            "external_temperature": s.external_temperature,
            "setpoint": s.setpoint,
            "mode": s.mode,
            "control_active": s.mode == "control",
            "circulation_running": s.mode in ("circulation", "control"),
            "alarm_active": s.alarm,
        }
        return f"Status: {status}", status, {}

    # Metrics (all read from the same cached status, i.e. normally one serial transaction per interval)
    # Seconds without any valid answer before going to ERROR
    max_down_seconds: float = 1.0
    _first_fail: float | None = None

    @schedule_metric("", 3)
    def DEVICE_ALIVE(self) -> bool:
        if not hasattr(self, "device") or self.fsm.state.name not in ("ORBIT", "RUN"):
            self._first_fail = None
            return True

        try:
            self.device.read_status(max_age=0)
            self._first_fail = None
            return True
        except Exception as e:
            now = time.monotonic()
            if self._first_fail is None:
                self._first_fail = now
            down = now - self._first_fail
            self.log.warning(f"Huber not responding for {down:.0f} s: {e}")
            if down >= self.max_down_seconds:
                self._on_connection_lost()
            return False

    def _on_connection_lost(self) -> None:
        msg = "Lost connection to Huber chiller"
        try:
            self.fsm.failure(msg)
            self._wrap_failure(msg)
        except Exception as e:
            self.log.error(f"Could not enter ERROR state: {e}")
        self.log.critical(msg)
    @schedule_metric("degC", 5)
    def INTERNAL_TEMP(self) -> Any:
        return self.device.get_internal_temperature()

    @schedule_metric("degC", 5)
    def EXTERNAL_TEMP(self) -> Any:
        # None if no external sensor is connected
        return self.device.get_external_temperature()

    @schedule_metric("degC", 5)
    def SETPOINT(self) -> Any:
        return self.device.get_setpoint()

    @schedule_metric("", 5)
    def CONTROL_ACTIVE(self) -> Any:
        return self.device.is_control_active()

    @schedule_metric("", 5)
    def CIRCULATION_RUNNING(self) -> Any:
        return self.device.is_circulation_running()

    @schedule_metric("", 5)
    def ALARM_ACTIVE(self) -> Any:
        return self.device.get_alarm_status()
