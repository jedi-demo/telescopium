---
# SPDX-FileCopyrightText: 2026 DESY and the Constellation authors
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "Huber"
description: "Satellite controlling a Huber Minichiller / Unichiller OLÉ temperature control unit via RS-232 or USB"
category: "Temperature Control"
---

## Description

This satellite controls Huber Minichiller / Unichiller OLÉ temperature control units remotely via their serial interface, either the RS-232 port or the USB device port of the unit (which appears as a virtual serial port on the host). It sets the temperature setpoint, starts and stops temperature control (circulation pump, compressor and heating) and publishes internal and external temperatures, control mode and alarm state as metrics.

The satellite communicates with the unit using two command sets described in chapter 6.1 of the operation manual:

- **LAI commands** (with length field and checksum) are used to identify the unit, read the setpoint limits and read the complete status. Mode, alarm state, setpoint, internal temperature and external temperature are obtained with a single `G` transaction, so the unit is queried only once per monitoring interval.
- **PP commands** are used to write the setpoint and to start or stop temperature control.

All responses are validated (length field and checksum for LAI, prefix and numeric value for PP), and the requested setpoint is checked against the setpoint limits reported by the unit before it is written.

```{note}
This satellite requires write access to the serial port or USB-to-serial converter the device is attached to. It is recommended to **not run this satellite with root privileges** but to allow the regular user access to the required port instead.
On Unix, if the port is e.g. `/dev/ttyUSB0` (USB-to-serial converter) or `/dev/ttyACM0` (USB device port of the unit), the group can be found via `stat /dev/ttyUSB0` (the Gid entry).
If the group is e.g. `dialout`, the permissions can be given with `sudo usermod -a -G dialout $USER`.
A restart is required for the changes to be effective.
```


## Supported devices

The device model to be controlled by this satellite is selected via the `device` configuration parameter. The following devices are currently supported:

### `MINICHILLER_300`

Huber Minichiller / Unichiller OLÉ, controlled via the LAI and PP command sets over a serial connection.

The serial transmission format is fixed to 8 data bits, 1 stop bit, no parity and no handshake and cannot be changed on the unit. Only the baud rate can be configured on the unit (9600 to 115200 baud). It has to match the `baudrate` parameter of the satellite.

The unit is a slave in a master-slave communication: it only answers requests and typically responds within 300 ms.
The satellite waits for each response before sending the next command and uses a read timeout of 2 s.

### Connection

The unit provides two ways to connect to the host, both standard on the controller:

| Interface | Typical port name | Notes |
|-----------|-------------------|-------|
| RS-232 | `/dev/ttyUSB0` (with a USB-to-serial converter) | Described in chapter 6.1 of the operation manual |
| USB device (micro-USB) | `/dev/ttyACM0` or `/dev/ttyUSB0` | Appears as a virtual serial port. The port name depends on the operating system and driver, check with `dmesg` or `ls /dev/tty*` after plugging in the cable |

On the unit, open the interface settings and select the interface that is connected (`RS232` or `USB device`). For the selected interface:

- Set **Baud rate** to the value used for the `baudrate` parameter of the satellite.
- Set **Mode** to **HuberBus**. The `STBus` mode is reserved for Huber service technicians and does not work with this satellite.

```{note}
The operation manual describes the command protocol in the chapter on the RS-232 interface. The USB device port offers the same baud rate and mode settings, and the satellite sends the same LAI and PP commands over it. If communication fails over USB, check the mode and baud rate on the unit first.
```

## Parameters

| Parameter | Description | Type | Default Value |
|-----------|-------------|------|---------------|
| `device` | Device model, see [supported devices](#supported-devices) | String | `MINICHILLER_300` |
| `port` | Serial port to connect to, e.g. `/dev/ttyUSB0` or `/dev/ttyACM0` | String | - |
| `baudrate` | Baud rate configured on the unit | Integer | `9600` |
| `setpoint` | Target temperature in °C. Must be within the setpoint limits reported by the unit | Float | `20.0` |
| `stop_on_landing` | Stop temperature control (pump, compressor and heating) when landing, on failure and on shutdown of the satellite | Boolean | `false` |

The parameters `device`, `port` and `baudrate` cannot be changed by reconfiguring. `setpoint` and `stop_on_landing` can.

## Metrics

| Metric | Description | Value Type | Interval |
|--------|-------------|------------|----------|
| `INTERNAL_TEMP` | Internal thermal fluid temperature (°C) | Float | 5s |
| `EXTERNAL_TEMP` | External Pt100 sensor temperature (°C). Not published if no external sensor is connected | Float | 5s |
| `SETPOINT` | Temperature setpoint currently set on the unit (°C) | Float | 5s |
| `CONTROL_ACTIVE` | Temperature control is active | Boolean | 5s |
| `CIRCULATION_RUNNING` | Thermal fluid circulation is running (circulation only or temperature control) | Boolean | 5s |
| `ALARM_ACTIVE` | An alarm or warning is pending on the unit | Boolean | 5s |

```{note}
`ALARM_ACTIVE` does not mean that control has stopped. Some conditions, e.g. "Tmax exceeded" or "Tmin undercut", are warnings for which the unit continues to control, while others, e.g. a low-level alarm or a sensor fault, switch control off. The alarm code is shown on the display of the unit. Refer to chapter 7.1 of the operation manual.
```

## Custom Commands

| Command | Description | Arguments | Return Value | Allowed States |
|---------|-------------|-----------|--------------|----------------|
| `identify` | Queries the identification string of the unit | - | String | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `get_chiller_status` | Reads internal and external temperature, setpoint, mode, control and circulation state, and alarm state | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `get_limits` | Reads setpoint limits and working range limits of the unit | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |
| `acknowledge_alarm` | Silences a pending audible alarm. The alarm condition itself is not cleared | - | String | All except `NEW`, `initializing`, `reconfiguring` and `ERROR` |

The dictionary returned by `get_chiller_status` contains the keys `internal_temperature`, `external_temperature` (`null` if no sensor is connected), `setpoint`, `mode` (`off`, `circulation` or `control`), `control_active`, `circulation_running` and `alarm_active`.

The dictionary returned by `get_limits` contains the keys `setpoint_min`, `setpoint_max`, `working_range_min` and `working_range_max`, all in °C.

## Troubleshooting

| Symptom | Possible cause |
|---------|----------------|
| `No complete response from Huber chiller` on initialization | Wrong `port` or `baudrate`, cable not connected, or the unit is off. The baud rate set on the unit has to match the `baudrate` parameter, and the interface **Mode** has to be `HuberBus` |
| Port does not exist after connecting via USB | The port name differs from the example (`/dev/ttyACM0` instead of `/dev/ttyUSB0`, `COMx` on Windows). Check `dmesg` after plugging in the cable, and make sure the cable carries data and not only power |
| `Permission denied` when opening the port | The user is not in the group owning the serial port, see the note above |
| `Invalid LAI response` / `checksum mismatch` | Noisy or unsuitable connection, or another program is using the same port |
| `Setpoint ... outside the allowed range` | `setpoint` is outside the limits of the unit. Read them with `get_limits` |
| `EXTERNAL_TEMP` is not published | No external Pt100 sensor is connected (the unit reports -151 °C in this case) |
