"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

This module provides the class for the PicoTC08 satellite.
"""

import ctypes
import time
from threading import Lock
from typing import Any

from picosdk.usbtc08 import usbtc08 as tc08

from constellation.core.commandmanager import cscp_requestable
from constellation.core.configuration import Configuration
from constellation.core.monitoring import schedule_metric
from constellation.core.protocol.cscp1 import SatelliteState, states_except
from constellation.core.satellite import Satellite

_VALID_TC_TYPES = {"B", "E", "J", "K", "N", "R", "S", "T"}
_MAX_TC08_CHANNELS = 8
_ACTIVE_STATES = states_except([SatelliteState.NEW, SatelliteState.initializing])


class PicoTC08(Satellite):
    """Satellite monitoring thermocouple channels and enforcing thermal interlocks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._lock = Lock()
        self.handle: int = 0
        self.num_channels: int = 0
        self.channel_limits: dict[int, float] = {}
        self._cached_temps: dict[int, float] = {}
        self._last_read: float = 0.0
        self._alarm_triggered: bool = False
        super().__init__(*args, **kwargs)

    def _configure_unit(self, mains_freq: int, tc_type_str: str, num_channels: int) -> None:
        """Apply mains rejection filter and configure active/inactive thermocouple channels."""
        # 0 = 50 Hz, 1 = 60 Hz
        if tc08.usb_tc08_set_mains(self.handle, 0 if mains_freq == 50 else 1) == 0:
            raise RuntimeError("Failed to configure TC-08 mains rejection frequency")

        tc_type = ctypes.c_int8(ord(tc_type_str))
        disabled_type = ctypes.c_int8(0)
        for ch in range(1, _MAX_TC08_CHANNELS + 1):
            channel_cfg = tc_type if ch <= num_channels else disabled_type
            if tc08.usb_tc08_set_channel(self.handle, ch, channel_cfg) == 0:
                raise RuntimeError(f"Failed to configure TC-08 channel {ch}")

    def do_initializing(self, config: Configuration) -> None:
        num_channels = int(config.get_num("num_channels", 4, min_val=1, max_val=_MAX_TC08_CHANNELS))
        raw_limits = config.get_array("max_temps", element_type=float)
        if len(raw_limits) != num_channels:
            raise ValueError(f"`max_temps` parameter requires {num_channels} values (got {len(raw_limits)})")

        tc_type_str = str(config.get("thermocouple_type", "K")).upper()
        if tc_type_str not in _VALID_TC_TYPES:
            raise ValueError(f"Unsupported `thermocouple_type` {tc_type_str!r}. Valid types: {sorted(_VALID_TC_TYPES)}")

        mains_freq = int(config.get_num("mains_freq", 50))
        if mains_freq not in (50, 60):
            raise ValueError(f"`mains_freq` must be 50 or 60 Hz, got {mains_freq}")

        with self._lock:
            if self.handle <= 0:
                self.handle = tc08.usb_tc08_open_unit()
                if self.handle <= 0:
                    raise RuntimeError(f"Cannot connect to Pico TC-08 (error code: {self.handle})")

            try:
                self._configure_unit(mains_freq, tc_type_str, num_channels)
            except RuntimeError:
                self._close_handle_unlocked()
                self.handle = tc08.usb_tc08_open_unit()
                if self.handle <= 0:
                    raise RuntimeError(f"Cannot reconnect to Pico TC-08 (error code: {self.handle})") from None
                self._configure_unit(mains_freq, tc_type_str, num_channels)

            self.num_channels = num_channels
            self.channel_limits = {ch: float(limit) for ch, limit in enumerate(raw_limits, start=1)}
            self._cached_temps = {}
            self._last_read = 0.0
            self._alarm_triggered = False

        self.log.info(
            "Pico TC-08 initialized (%d channels, type %s). Limits (degC): %s",
            self.num_channels,
            tc_type_str,
            self.channel_limits,
        )

    def do_reconfigure(self, partial_config: Configuration) -> str:
        config_keys = partial_config.get_keys()

        for key in ("num_channels", "thermocouple_type", "mains_freq"):
            if key in config_keys:
                raise ValueError(f"Reconfiguring `{key}` is not possible without reinitializing")

        if "max_temps" in config_keys:
            raw_limits = partial_config.get_array("max_temps", element_type=float)
            if len(raw_limits) != self.num_channels:
                raise ValueError(f"`max_temps` parameter requires {self.num_channels} values")
            with self._lock:
                self.channel_limits = {ch: float(limit) for ch, limit in enumerate(raw_limits, start=1)}
                self._alarm_triggered = False
            self.log.info("Updated channel temperature limits (degC): %s", self.channel_limits)

        return f"TC-08 limits: {self.channel_limits}"

    def do_run(self) -> str:
        while not self.stop_requested():
            self._read_temperatures(max_age=0.5)
            time.sleep(0.5)
        return "TC-08 monitoring stopped"

    def fail_gracefully(self) -> None:
        # Keep the USB handle open in ERROR state so continuous temperature monitoring remains active
        pass

    def reentry(self) -> None:
        self._close_device()
        super().reentry()

    def _close_handle_unlocked(self) -> None:
        if self.handle > 0:
            try:
                tc08.usb_tc08_close_unit(self.handle)
            except Exception as exc:
                self.log.warning("Could not close Pico TC-08 handle: %s", exc)
            finally:
                self.handle = 0

    def _close_device(self) -> None:
        """Close the USB TC-08 device handle if open."""
        with self._lock:
            self._close_handle_unlocked()
            self._cached_temps = {}
            self._last_read = 0.0

    def _trigger_safety_error(self, message: str) -> None:
        """Transition the satellite to ERROR state to trigger constellation-wide safety actions."""
        self.log.critical(message)

        current_state = getattr(self.fsm, "state", None)
        if current_state == SatelliteState.ERROR:
            self._alarm_triggered = True
            return

        if current_state == SatelliteState.RUN:
            self._alarm_triggered = True
            raise RuntimeError(message)

        if self._alarm_triggered:
            return

        triggers = [
            getattr(self.fsm, "error", None),
            getattr(self.fsm, "_error", None),
            getattr(self, "_error", None),
            getattr(self, "_fail", None),
        ]
        for attr_name in dir(self.fsm):
            if ("error" in attr_name or "fail" in attr_name) and not attr_name.startswith("__"):
                fn = getattr(self.fsm, attr_name, None)
                if callable(fn) and fn not in triggers:
                    triggers.append(fn)

        for trigger in triggers:
            if not callable(trigger):
                continue
            try:
                trigger(message)
                self._alarm_triggered = True
                return
            except TypeError:
                try:
                    trigger()
                    self._alarm_triggered = True
                    return
                except Exception as exc:
                    self.log.debug("FSM error trigger %r failed: %s", trigger, exc)
            except Exception as exc:
                self.log.debug("FSM error trigger %r failed: %s", trigger, exc)

        self.log.error("Failed to transition Pico TC-08 to ERROR state from %s", current_state)

    def _read_temperatures(self, max_age: float = 0.8) -> dict[int, float]:
        """
        Read all configured channels in a single USB transaction and verify thermal limits.
        Returns cached readings if younger than `max_age` seconds.
        """
        with self._lock:
            if self.handle <= 0 or not self.channel_limits:
                return {}

            now = time.monotonic()
            if self._cached_temps and max_age > 0 and (now - self._last_read) <= max_age:
                return self._cached_temps

            temp = (ctypes.c_float * 9)()
            overflow = ctypes.c_int16()
            status = tc08.usb_tc08_get_single(self.handle, ctypes.byref(temp), ctypes.byref(overflow), 0)
            if status <= 0:
                raise RuntimeError(f"Failed to read temperatures from Pico TC-08 (status: {status})")

            readings: dict[int, float] = {}
            for ch, limit in self.channel_limits.items():
                if overflow.value & (1 << ch):
                    self.log.warning("Overflow or open thermocouple detected on TC-08 channel %d", ch)

                val = float(temp[ch])
                readings[ch] = val

                if val > limit:
                    self._trigger_safety_error(
                        f"THERMAL INTERLOCK: Channel {ch} reached {val:.2f} degC (limit: {limit:.2f} degC)"
                    )

            self._cached_temps = readings
            self._last_read = now
            return readings

    def _get_channel_temp(self, channel: int) -> float | None:
        """Return temperature for an active channel, or None if inactive or uninitialized."""
        if self.handle <= 0 or channel not in self.channel_limits:
            return None
        return self._read_temperatures().get(channel)

    @cscp_requestable(states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring]))
    def get_temperatures(self) -> tuple[str, Any, dict[str, Any]]:
        """Read current temperatures and configured limits for all active channels."""
        temps = self._read_temperatures(max_age=0)
        payload = {
            f"ch{ch}": {"temperature": temps[ch], "limit": self.channel_limits[ch]}
            for ch in self.channel_limits
            if ch in temps
        }
        return f"Temperatures: {temps}", payload, {}

    @cscp_requestable(states_except([SatelliteState.NEW, SatelliteState.initializing, SatelliteState.reconfiguring]))
    def get_limits(self) -> tuple[str, Any, dict[str, Any]]:
        """Return configured temperature limits per channel."""
        return f"Limits: {self.channel_limits}", {f"ch{k}": v for k, v in self.channel_limits.items()}, {}

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH1(self) -> Any:
        return self._get_channel_temp(1)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH2(self) -> Any:
        return self._get_channel_temp(2)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH3(self) -> Any:
        return self._get_channel_temp(3)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH4(self) -> Any:
        return self._get_channel_temp(4)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH5(self) -> Any:
        return self._get_channel_temp(5)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH6(self) -> Any:
        return self._get_channel_temp(6)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH7(self) -> Any:
        return self._get_channel_temp(7)

    @schedule_metric("degC", 1, _ACTIVE_STATES)
    def TEMP_CH8(self) -> Any:
        return self._get_channel_temp(8)
