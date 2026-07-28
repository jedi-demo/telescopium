"""
SPDX-FileCopyrightText: 2024 DESY and the Constellation authors
SPDX-License-Identifier: EUPL-1.2

Provides the class for the AIM-TTi QL355TP  satellite.
"""

import time
from typing import Any

from constellation.core.configuration import Configuration
from constellation.core.satellite import Satellite
from constellation.core.commandmanager import cscp_requestable
from constellation.core.fsm import SatelliteState
from constellation.core.message.cscp1 import CSCP1Message
from constellation.core.monitoring import schedule_metric

from .QL355TPII import QL355TPII

sampling_interval = 1.0 # Interval in seconds for metric sampling (e.g., voltage, current, status)

class TTiQLSatellite(Satellite):
    """
    Satellite controlling an AIM-TTi QL355TP  power supply.
    
    Provides independent channel control, sequenced launching, 
    and voltage ramping capabilities.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = None
        self.ip = None

    def do_initializing(self, config: Configuration) -> str:
        """Read configuration, establish connection, and apply limits."""
        self.ip = config["ip"] # IP address of the TTi device on the network
        
        self.v1 = config["voltage1"] # Target voltage for channel 1 (in volts)
        self.i1 = config["current_limit1"]  # Current limit for channel 1 (in amps)
        self.ovp1 = config["ovp1"] # Over-voltage protection for channel 1 (in volts)
        self.ocp1 = config["ocp1"] # Over-current protection for channel 1 (in amps)

        self.v2 = config["voltage2"] # Target voltage for channel 2 (in volts)
        self.i2 = config["current_limit2"] # Current limit for channel 2 (in amps)
        self.ovp2 = config["ovp2"] # Over-voltage protection for channel 2 (in volts)
        self.ocp2 = config["ocp2"] # Over-current protection for channel 2 (in amps)
        
        self.enable_ch1 = config["enable_ch1"] # Whether to include channel 1 in the launch sequence (True/False)
        self.enable_ch2 = config["enable_ch2"] # Whether to include channel 2 in the launch sequence (True/False)
        self.launch_order = config["launch_order"] # List defining the sequence of channel activation (e.g., [1, 2] or [2, 1])
        self.delay_between = config["delay_between"] # Delay in seconds between channel activations in the launch sequence
        self.start_delay = config["start_delay"] # Delay in seconds before starting the launch sequence

        self.ramp_ch1 = config["ramp_ch1"] # Whether to apply a voltage ramp on channel 1 during launch/landing (True/False)
        self.ramp_ch2 = config["ramp_ch2"] # Whether to apply a voltage ramp on channel 2 during launch/landing (True/False)
        self.ramp_step = config["ramp_step"] # Voltage step for the ramp (in volts)
        self.ramp_delay = config["ramp_delay"] # Delay in seconds between voltage steps (in seconds)

        try:
            self.device = QL355TPII(self.ip)
            self.device.initialize()
            idn = self.device.identify()
            self.log.info(f"Connected to TTi: {idn}")
        except Exception as e:
            raise RuntimeError(f"Connection failed to {self.ip}: {e}")
        
        self.device.set_ovp(1, self.ovp1)
        self.device.set_ocp(1, self.ocp1)
        self.device.set_current_limit(1, self.i1)
        self.device.set_ovp(2, self.ovp2)
        self.device.set_ocp(2, self.ocp2)
        self.device.set_current_limit(2, self.i2)
        
        if self.ramp_ch1: 
            self.device.set_voltage(1, 0.0)
        else: 
            self.device.set_voltage(1, self.v1)
            
        if self.ramp_ch2: 
            self.device.set_voltage(2, 0.0)
        else: 
            self.device.set_voltage(2, self.v2)
        
        return f"Initialized at {self.ip}"

    def do_launching(self) -> str:
        """Start the active channels according to the defined sequence and ramps."""
        if self.start_delay > 0:
            time.sleep(self.start_delay)
        msg = []
        for ch in self.launch_order:
            if str(ch).lower() == "both":
                self._launch_both_channels()
                msg.append("CH1+CH2 ON")
                time.sleep(self.delay_between)        
            elif ch == 1 and self.enable_ch1:
                self._launch_channel(1, self.v1, self.ramp_ch1)
                msg.append("CH1 ON")
                time.sleep(self.delay_between)
            elif ch == 2 and self.enable_ch2:
                self._launch_channel(2, self.v2, self.ramp_ch2)
                msg.append("CH2 ON")
                time.sleep(self.delay_between)
        
        if not msg: 
            return "No channels enabled"
        return " -> ".join(msg)

    def do_landing(self) -> str:
        """Stop the active channels using the reverse sequence."""
        landing_order = reversed(self.launch_order)
        for ch in landing_order:
            if str(ch).lower() == "both":
                self._land_both_channels()
                time.sleep(self.delay_between)
            elif ch == 1 and self.enable_ch1:
                self._land_channel(1, self.v1, self.ramp_ch1)
                time.sleep(self.delay_between)
            elif ch == 2 and self.enable_ch2:
                self._land_channel(2, self.v2, self.ramp_ch2)
                time.sleep(self.delay_between)
        
        return "Landing sequence completed"

    def _launch_channel(self, ch: int, target_v: float, do_ramp: bool):
        """Helper method to turn on a channel, applying a ramp if configured."""
        if do_ramp:
            self.log.info(f"RAMP CH{ch}: Ramping up to {target_v}V...")
            current_v = 0.0
            self.device.set_voltage(ch, current_v)
            self.device.set_output(ch, True)
            
            while current_v < target_v:
                current_v += self.ramp_step
                if current_v > target_v: 
                    current_v = target_v
                self.device.set_voltage(ch, current_v)
                self.log.info(f"RAMP CH{ch}: {current_v}V")
                time.sleep(self.ramp_delay)
        else:
            self.device.set_voltage(ch, target_v)
            self.device.set_output(ch, True)
    def _launch_both_channels(self):
        """Helper method to turn on both channels simultaneously, interleaving ramps if needed."""
        ramp1 = self.ramp_ch1 and self.enable_ch1
        ramp2 = self.ramp_ch2 and self.enable_ch2

        if self.enable_ch1:
            self.device.set_voltage(1, 0.0 if ramp1 else self.v1)
            self.device.set_output(1, True)
            
        if self.enable_ch2:
            self.device.set_voltage(2, 0.0 if ramp2 else self.v2)
            self.device.set_output(2, True)

        if ramp1 or ramp2:
            self.log.info("RAMP CH1+CH2: Ramping up...")
            current_v1, current_v2 = 0.0, 0.0

            while (ramp1 and current_v1 < self.v1) or (ramp2 and current_v2 < self.v2):
                if ramp1 and current_v1 < self.v1:
                    current_v1 = min(current_v1 + self.ramp_step, self.v1)
                    self.device.set_voltage(1, current_v1)
                
                if ramp2 and current_v2 < self.v2:
                    current_v2 = min(current_v2 + self.ramp_step, self.v2)
                    self.device.set_voltage(2, current_v2)
                
                self.log.info(f"RAMP CH1+CH2: CH1={current_v1}V, CH2={current_v2}V")
                time.sleep(self.ramp_delay)
                
    def _land_channel(self, ch: int, target_v: float, do_ramp: bool):
        """Helper method to turn off a channel, applying a downward ramp if configured."""
        if do_ramp:
            self.log.info(f"RAMP CH{ch}: Ramping down from {target_v}V...")
            current_v = target_v
            while current_v > 0:
                current_v -= self.ramp_step
                if current_v < 0: 
                    current_v = 0.0
                self.device.set_voltage(ch, current_v)
                self.log.info(f"RAMP CH{ch}: {current_v}V")
                time.sleep(self.ramp_delay)
                
        self.device.set_output(ch, False)
    def _land_both_channels(self):
        """Helper method to turn off both channels simultaneously, interleaving downward ramps if needed."""
        ramp1 = self.ramp_ch1 and self.enable_ch1
        ramp2 = self.ramp_ch2 and self.enable_ch2

        if ramp1 or ramp2:
            self.log.info("RAMP CH1+CH2: Ramping down...")
            current_v1 = self.v1 if ramp1 else 0.0
            current_v2 = self.v2 if ramp2 else 0.0

            while (ramp1 and current_v1 > 0) or (ramp2 and current_v2 > 0):
                if ramp1 and current_v1 > 0:
                    current_v1 = max(current_v1 - self.ramp_step, 0.0)
                    self.device.set_voltage(1, current_v1)
                
                if ramp2 and current_v2 > 0:
                    current_v2 = max(current_v2 - self.ramp_step, 0.0)
                    self.device.set_voltage(2, current_v2)
                
                self.log.info(f"RAMP CH1+CH2: CH1={current_v1}V, CH2={current_v2}V")
                time.sleep(self.ramp_delay)
                
        if self.enable_ch1: self.device.set_output(1, False)
        if self.enable_ch2: self.device.set_output(2, False)
        
    def reentry(self) -> None:
        """Safely close the device connection on shutdown."""
        if self.device: self.device.close()
        super().reentry()

    def do_reconfigure(self, partial_config: Configuration) -> str:
        """Handle dynamic configuration updates from the network."""
        if "voltage1" in partial_config:
            self.v1 = partial_config["voltage1"]
            self.device.set_voltage(1, self.v1)
        if "current_limit1" in partial_config:
            self.i1 = partial_config["current_limit1"]
            self.device.set_current_limit(1, self.i1)
        if "voltage2" in partial_config:
            self.v2 = partial_config["voltage2"]
            self.device.set_voltage(2, self.v2)
        if "current_limit2" in partial_config:
            self.i2 = partial_config["current_limit2"]
            self.device.set_current_limit(2, self.i2)
        return "Reconfigured"

    # --------------------------------------------------------------------------
    # METRICS
    # --------------------------------------------------------------------------

    @schedule_metric("V", sampling_interval)
    def CH1_V(self):
        if self.device: return self.device.get_voltage_readback(1)
        return 0.0
   
    @schedule_metric("A", sampling_interval)
    def CH1_I(self):
        if self.device: return self.device.get_current_readback(1)
        return 0.0
   
    @schedule_metric("bitmask", sampling_interval)
    def CH1_STAT(self):
        if self.device: return self.device.get_limit_status(1)
        return 0
   
    @schedule_metric("V", sampling_interval)
    def CH2_V(self):
        if self.device: return self.device.get_voltage_readback(2)
        return 0.0
   
    @schedule_metric("A", sampling_interval)
    def CH2_I(self):
        if self.device: return self.device.get_current_readback(2)
        return 0.0
   
    @schedule_metric("bitmask", sampling_interval) 
    def CH2_STAT(self):
        if self.device: return self.device.get_limit_status(2)
        return 0

    @schedule_metric("code", sampling_interval)
    def LAST_ERROR(self):
        if self.device:
            err = self.device.get_execution_error()
            if err != 0:
                self.log.warning(f"Device Error Code: {err}")
            return err
        return 0

    # --------------------------------------------------------------------------
    # CUSTOM COMMANDS
    # --------------------------------------------------------------------------

    @cscp_requestable
    def set_live_voltage(self, request: CSCP1Message) -> tuple[str, Any, dict]:
        """Change the target voltage of a specific channel dynamically."""
        data = request.payload
        channel = 1
        voltage = 0.0

        if isinstance(data, (int, float)):
            voltage = float(data)
        elif isinstance(data, dict):
            channel = data.get("ch", 1)
            voltage = float(data.get("v", 0.0))
        
        if self.device:
            self.device.set_voltage(channel, voltage)
            self.log.info(f"Live Voltage CH{channel} -> {voltage} V")
            return f"CH{channel} set to {voltage}V", voltage, {}
        else:
            return "Error: Device not connected", None, {}

    @cscp_requestable
    def set_live_current(self, request: CSCP1Message) -> tuple[str, Any, dict]:
        """Change the current limit of a specific channel dynamically."""
        data = request.payload
        channel = 1
        current = 0.0

        if isinstance(data, (int, float)):
            current = float(data)
        elif isinstance(data, dict):
            channel = data.get("ch", 1)
            current = float(data.get("i", 0.0))

        if self.device:
            self.device.set_current_limit(channel, current)
            self.log.info(f"Live Current CH{channel} -> {current} A")
            return f"CH{channel} limit set to {current}A", current, {}
        return "Error", None, {}

    @cscp_requestable
    def set_output_state(self, request: CSCP1Message) -> tuple[str, Any, dict]:
        """Turn a specific channel ON or OFF dynamically."""
        data = request.payload
        channel = 1
        state = False
        
        if isinstance(data, bool) or isinstance(data, int):
            state = bool(data)
        elif isinstance(data, dict):
            channel = data.get("ch", 1)
            state = bool(data.get("on", False))

        if self.device:
            self.device.set_output(channel, state)
            s_str = "ON" if state else "OFF"
            self.log.info(f"Output CH{channel} -> {s_str}")
            return f"CH{channel} is {s_str}", state, {}
        return "Error", None, {}
