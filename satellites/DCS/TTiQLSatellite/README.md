---
# SPDX-FileCopyrightText: 2024 DESY and the Constellation authors
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "TTiQL"
description: "Satellite controlling an AIM-TTi QL355TP power supply"
---

## Description

This satellite uses a TCP/IP socket connection to control AIM-TTi QL Series power supplies remotely (e.g., QL355TP). The satellite can control dual-channel SMUs, operate them as voltage sources, and manage current limits independently. 

Among other settings, it is possible to configure over-voltage protection (OVP), over-current protection (OCP), and sequence the launch order of the channels. Voltage changes and power-up sequences can be performed as controlled ramps or direct step inputs.

## Requirements

The power supply must be connected to the local network (LAN) and be reachable via its IP address. The default TCP port used for SCPI communication is `9221`.

## Supported devices

This satellite has been explicitly developed and tested with the **AIM-TTi QL355TP Series II**. 

However, because it implements standard AIM-TTi SCPI commands over a raw TCP/IP socket (port 9221), it is highly likely to work out-of-the-box with other LAN-enabled dual-channel power supplies from AIM-TTi (such as the rest of the QL-P, PL-P, or CPX series). 

Users with single-channel or triple-channel variants may need to adapt the hardcoded two-channel logic in the satellite implementation.

### `QL355TPII`
Supports dual independent outputs with dynamic voltage and current limit adjustments.

## Parameters

| Parameter | Description | Type | Example |
|-----------|-------------|------|---------------|
| `ip` | IP address of the instrument on the network | String | 192.168.1.15 |
| `enable_ch1` | Enable output for Channel 1 on launch | Bool | `true` / `false` |
| `enable_ch2` | Enable output for Channel 2 on launch | Bool | `true` / `false` |
| `launch_order` | Sequence order for turning on channels | List | `[1, 2]` / `[2, 1]` |
| `delay_between`| Time to wait between turning on channels (in seconds) | Float | `1.0` |
| `start_delay` | Initial safety delay before starting launch sequence (in seconds) | Float | `0.0` |
| `ramp_ch1` | Enable voltage ramping for Channel 1 | Bool | `true` / `false` |
| `ramp_ch2` | Enable voltage ramping for Channel 2 | Bool | `true` / `false` |
| `ramp_step` | Voltage increment per step during ramping (in volts) | Float | `1.0` |
| `ramp_delay` | Wait time between voltage increments (in seconds) | Float | `1.0` |
| `voltage1` | Target output voltage for Channel 1 (in volts) | Float | `0.0` |
| `current_limit1`| Current limit in Ampere for Channel 1 (in amps) | Float | `1.0` |
| `ovp1` | Over-voltage protection limit for Channel 1 (in volts) | Float | `1.0` |
| `ocp1` | Over-current protection limit for Channel 1 (in amps) | Float | `2` |
| `voltage2` | Target output voltage for Channel 2 (in volts) | Float | `0.0` |
| `current_limit2`| Current limit in Ampere for Channel 2 (in amps) | Float | `1.0` |
| `ovp2` | Over-voltage protection limit for Channel 2 (in volts) | Float | `1` |
| `ocp2` | Over-current protection limit for Channel 2 (in amps) | Float | `2` |

## Metrics

| Metric | Description | Value Type | Metric Type | Interval |
|--------|-------------|------------|-------------|----------|
| `CH1_V` | Voltage output readback Channel 1 | Float | `LAST_VALUE` | 2.0s |
| `CH1_I` | Current output readback Channel 1 | Float | `LAST_VALUE` | 2.0s |
| `CH1_STAT` | Limit Status Register Channel 1 | Integer | `LAST_VALUE` | 2.0s |
| `CH2_V` | Voltage output readback Channel 2 | Float | `LAST_VALUE` | 2.0s |
| `CH2_I` | Current output readback Channel 2 | Float | `LAST_VALUE` | 2.0s |
| `CH2_STAT` | Limit Status Register Channel 2 | Integer | `LAST_VALUE` | 2.0s |
| `LAST_ERROR`| Execution Error Code | Integer | `LAST_VALUE` | 2.0s |

## Custom Commands

| Command | Description | Arguments | Return Value | Allowed States |
|---------|-------------|-----------|--------------|----------------|
| `set_live_voltage` | Changes the target voltage dynamically | `dict` (`ch`, `v`) | Float | `ORBIT`, `RUN` |
| `set_live_current` | Changes the current limit dynamically | `dict` (`ch`, `i`) | Float | `ORBIT`, `RUN` |
| `set_output_state` | Turns a specific channel ON or OFF | `dict` (`ch`, `on`) | Bool | `ORBIT`, `RUN` |
