"""TJ-Monopix2 Constellation receiver writing ScanBase-compatible HDF5 files.

Each sender is treated as one chip. Consequently, an output file has this
layout::

    /
      configuration_in/
      raw_data
      meta_data
      configuration_out/

The contents of every ``/`` group use the same node names, PyTables
filters, raw-data EArray, and metadata table schema as ``ScanBase``.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any

from constellation.core.configuration import Configuration
from constellation.core.message.cdtp2 import DataRecord
from constellation.core.monitoring import schedule_metric
from constellation.core.receiver_satellite import ReceiverSatellite

from .h5_writer import ScanBaseH5Writer


class TJMonopix2H5Receiver(ReceiverSatellite):
    """
    Constellation satellite that writes TJ-Monopix2 data in ScanBase format.

    Responsibilities:
      - Constellation lifecycle (do_initializing, do_starting, do_stopping, fail_gracefully)
      - Config handling (output_directory, flush_interval)
      - Resolving chip configuration from BOR/EOR user_tags
      - Record counting and flush timing
      - Delegating all HDF5 work to ScanBaseH5Writer
    """

    def do_initializing(self, config: Configuration) -> str:
        self.output_directory = config.get_path("output_directory")
        self.flush_interval: float = config.get_num("flush_interval", 10.0)

        self.writer = ScanBaseH5Writer()
        self.record_counts: dict[str, int] = {}
        self._seen_eor: set[str] = set()
        self._last_flush: datetime.datetime | None = None

        return f"Initialized receiver, output directory: {self.output_directory}"

    def do_starting(self, run_identifier: str) -> str:
        self.record_counts.clear()
        self._seen_eor.clear()
        self._last_flush = datetime.datetime.now(datetime.UTC)

        self.writer.open_run(self.output_directory, run_identifier, log=self.log)
        return f"Started run {run_identifier}"

    def do_stopping(self) -> str:
        self.writer.close()
        return "Run stopped"

    def fail_gracefully(self) -> str:
        try:
            self.writer.close()
        except Exception:
            pass
        return "Receiver failed gracefully"

    # --------------------------------------------------------------------- #
    # Constellation callbacks
    # --------------------------------------------------------------------- #

    def receive_bor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        configuration: dict[str, Any],
    ) -> None:
        """Store BOR metadata and ScanBase-style configuration_in."""
        chip_config = _resolve_chip_config(sender, user_tags)
        if chip_config is None:
            self.log.warning(
                "BOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(user_tags.get("chips", {})),
            )
        self.writer.write_bor(sender, user_tags, configuration, chip_config)
        self.record_counts.setdefault(sender, 0)
        self._maybe_flush()

    def receive_eor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        run_metadata: dict[str, Any],
    ) -> None:
        """Store EOR metadata and ScanBase-style configuration_out."""
        if sender in self._seen_eor:
            self.log.warning("Duplicate EOR received from %s; ignoring it", sender)
            return

        chip_config = _resolve_chip_config(sender, user_tags)
        if chip_config is None:
            self.log.warning(
                "EOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(user_tags.get("chips", {})),
            )
        self.writer.write_eor(sender, user_tags, run_metadata, chip_config)
        self._seen_eor.add(sender)
        self._maybe_flush()

    def receive_data(self, sender: str, data_record: DataRecord) -> None:
        """Append a DataRecord using ScanBase.handle_data semantics."""
        self.writer.append_record(sender, data_record)
        self.record_counts[sender] = self.record_counts.get(sender, 0) + 1
        self._maybe_flush()

    # --------------------------------------------------------------------- #
    # Metrics
    # --------------------------------------------------------------------- #

    @schedule_metric("int", 5)
    def total_records_received(self) -> int:
        return sum(self.record_counts.values())

    @schedule_metric("filename", 5)
    def currently_open_filename(self) -> str | None:
        return self.writer.filename

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    def _maybe_flush(self) -> None:
        if self.flush_interval <= 0 or self._last_flush is None:
            return

        now = datetime.datetime.now(datetime.UTC)
        if (now - self._last_flush).total_seconds() >= self.flush_interval:
            self.writer.flush()
            self._last_flush = now


def _resolve_chip_config(
    sender: str,
    user_tags: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Find the chip configuration for this sender.

    If there is no direct match but only one chip in the BOR/EOR,
    fall back to that single chip (legacy behaviour).
    """
    chips = user_tags.get("chips", {})
    if not isinstance(chips, Mapping):
        return None

    chip_config = chips.get(sender)
    if chip_config is not None:
        return chip_config

    if len(chips) == 1:
        return next(iter(chips.values()))

    return None