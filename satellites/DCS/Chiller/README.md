---
# SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "Huber"
description: "Satellite controlling a Huber Minichiller / Unichiller OLÉ temperature control unit"
category: "Temperature Control"
language: "Python"
parent_class: "Satellite"
---

## Description

This satellite uses the serial port (RS-232 or USB device port) of Huber Minichiller / Unichiller OLÉ temperature control units to control them remotely. The satellite can control a single unit. Among other settings, it is possible to configure the target temperature (setpoint) and whether temperature control is stopped when landing. When launching, the setpoint is written and temperature control (circulation pump, compressor and heating) is started.

The satellite monitors the unit continuously. If the unit stops answering while the satellite is in `ORBIT` or `RUN`, the satellite goes to the `ERROR` state.

```{note}
This satellite requires write access to the serial port or USB-to-serial converter the device is attached to. It is recommended to **not run this satellite with root privileges** but to allow the regular user access to the required port instead. 
On Unix, if the port is e.g. `/dev/ttyUSB0`, the group can be found via `stat /dev/ttyUSB0` (the Gid entry). 
If the group is e.g. `dialout`, the permissions can be given with `sudo usermod -a -G dialout $USER`.
A restart is required for the changes to be effective.
```

## Supported devices

The device model to be controlled by this satellite can be selected via the `device` configuration parameter. The following devices are currently supported:

### `MINICHILLER_300`

The `MINICHILLER_300` device supports the Huber Minichiller / Unichiller OLÉ, controlled via the LAI and PP command sets described in chapter 6.1 of the operation manual. Responses are validated (length field and checksum for LAI, prefix and value for PP) and the setpoint is checked against the limits reported by the unit before it is written.

The following settings are required and have to be configured on the unit before starting the satellite, in the interface settings of the connected interface (`RS232` or `USB device`):

- `Baud rate`: same value as the `baudrate` parameter (`9600` to `115200`)
- `Mode`: `HuberBus` (`STBus` is reserved for service technicians and does not work)

The serial format is fixed to 8 data bits, 1 stop bit, no parity and no handshake and cannot be changed on the unit.

Possible ports are e.g. `/dev/ttyUSB0` (RS-232 with USB-to-serial converter) and `/dev/ttyACM0` or `/dev/ttyUSB0` (USB device port, appears as a virtual serial port). The name depends on the operating system and driver, it can be found with `dmesg` after plugging in the cable.

Supported setpoint values: the range reported by the unit (see the `get_limits` command).

## Parameters

| Parameter | Description | Type | Default Value |
|-----------|-------------|------|---------------|
| `device` | Device | String | `MINICHILLER_300` |
| `port` | Serial port to connect to | String | - |
| `baudrate` | Baud rate configured on the unit | Integer | `9600` |
| `setpoint` | Target temperature in °C. Must be within the setpoint limits of the unit | Float | `20.0` |
| `stop_on_landing` | Stop temperature control (pump, compressor and heating) when landing, on failure and on shutdown of the satellite. If `false`, control keeps running | Boolean | `false` |

The parameters `device`, `port` and `baudrate` cannot be changed by reconfiguring. `setpoint` and `stop_on_landing` can.

## Metrics

| Metric | Description | Value Type | Interval |
|--------|-------------|------------|----------|
| `DEVICE_ALIVE` | If the unit answers (only checked in `ORBIT` and `RUN`) | Bool | 3s |
| `INTERNAL_TEMP` | Internal thermal fluid temperature in °C | Float | 5s |
| `EXTERNAL_TEMP` | External Pt100 temperature in °C (not published if no sensor is connected) | Float | 5s |
| `SETPOINT` | Setpoint currently set on the unit in °C | Float | 5s |
| `CONTROL_ACTIVE` | If temperature control is active | Bool | 5s |
| `CIRCULATION_RUNNING` | If the thermal fluid circulation is running | Bool | 5s |
| `ALARM_ACTIVE` | If an alarm or warning is pending on the unit | Bool | 5s |

```{note}
`ALARM_ACTIVE` does not mean that control has stopped. Some conditions (e.g. "Tmax exceeded") are warnings for which the unit continues to control, others (e.g. low level or sensor fault) switch control off. The alarm code is shown on the display of the unit, see chapter 7.1 of the operation manual.
```

## Custom Commands

| Command | Description | Arguments | Return Value | Allowed States |
|---------|-------------|-----------|--------------|----------------|
| `identify` | Identify the unit | - | String | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `get_chiller_status` | Reads temperatures, setpoint, mode and alarm state | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `get_limits` | Reads setpoint limits and working range | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `acknowledge_alarm` | Silences a pending audible alarm (the alarm condition is not cleared) | - | String | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |

The dictionary returned by `get_chiller_status` contains `internal_temperature`, `external_temperature` (`null` if no sensor is connected), `setpoint`, `mode` (`off`, `circulation` or `control`), `control_active`, `circulation_running` and `alarm_active`.

The dictionary returned by `get_limits` contains `setpoint_min`, `setpoint_max`, `working_range_min` and `working_range_max`, all in °C.