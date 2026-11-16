---
# SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
# SPDX-License-Identifier: CC-BY-4.0 OR EUPL-1.2
title: "PicoTC08"
description: "Satellite monitoring a Pico Technology USB TC-08 thermocouple data logger"
category: "Environment Sensors"
---

## Description

This satellite uses the PicoSDK USB interface to read temperatures from a Pico Technology USB TC-08 thermocouple data logger. It supports up to 8 thermocouple channels and operates both as a continuous temperature monitor and as a thermal safety interlock for the constellation.

Each active channel is evaluated against an individual upper temperature threshold (`max_temps`). If any channel exceeds its configured limit, the satellite immediately transitions to the `ERROR` state, forcing `DYNAMIC` and `ESSENTIAL` satellites in the group into `SAFE` mode. Temperature telemetry and read commands remain active in both `SAFE` and `ERROR` states so system cooldown can be continuously monitored.

```{note}
This satellite requires read/write access to the USB device node of the Pico TC-08. It is recommended to **not run this satellite with root privileges** but to configure a `udev` rule granting a non-root group (such as `plugdev` or `pico`) access to Pico Technology USB devices (`idVendor=="0ce9"`).
```

```{note}
To ensure other satellites react to a thermal interlock by entering `SAFE` mode, keep the default autonomy role (`DYNAMIC`) or explicitly set `role = "ESSENTIAL"` in the satellite configuration.
```

## Requirements

The `PicoTC08` satellite requires the PicoSDK C driver library (`libusbtc08`) installed on the host system, as well as the `picosdk` Python wrapper:

```sh
pip install picosdk
```

## Supported devices

The following data logger model is supported:

### `USB TC-08`

The Pico Technology USB TC-08 8-channel thermocouple data logger.
Supported thermocouple types are `B`, `E`, `J`, `K`, `N`, `R`, `S`, and `T`.

## Parameters

| Parameter | Description | Type | Default Value |
|-----------|-------------|------|---------------|
| `num_channels` | Number of active thermocouple channels (`1` to `8`, starting from channel 1) | Integer | `4` |
| `max_temps` | Upper temperature limit in °C for each active channel (length must match `num_channels`) | List of Floats | - |
| `thermocouple_type` | Thermocouple type applied to all active channels (`B`, `E`, `J`, `K`, `N`, `R`, `S`, `T`) | String | `"K"` |
| `mains_freq` | Mains rejection filter frequency in Hz (`50` or `60`) | Integer | `50` |

## Metrics

| Metric | Description | Value Type | Interval |
|--------|-------------|------------|----------|
| `TEMP_CH1` .. `TEMP_CH8` | Temperature reading in °C for active channels `1` through `num_channels` (published continuously in all states, including `SAFE` and `ERROR`) | Float | 1s |

## Custom Commands

| Command | Description | Arguments | Return Value | Allowed States |
|---------|-------------|-----------|--------------|----------------|
| `get_temperatures` | Forces an immediate USB readout and returns current temperatures and configured limits for all active channels | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` |
| `get_limits` | Returns the configured upper temperature limits (°C) per active channel | - | Dictionary | All except `NEW`, `initializing`, `reconfiguring` |

## Example Configuration

```toml
[PicoTC08.tc08]
num_channels = 4
max_temps = [35.0, 50.0, 60.0, 40.0]
thermocouple_type = "K"
mains_freq = 50

[PicoTC08.tc08._autonomy]
role = "ESSENTIAL"
```
