"""TJ-Monopix2 Constellation receiver writing ScanBase-compatible HDF5 files.

Each sender is treated as one chip. Consequently, an output file has this
layout::

    /<sender>/
      configuration_in/
      raw_data
      meta_data
      configuration_out/

The contents of every ``<sender>`` group use the same node names, PyTables
filters, raw-data EArray, and metadata table schema as ``ScanBase``.
"""

from __future__ import annotations

import datetime
import pathlib
from collections.abc import Mapping
from typing import Any

import tables as tb

from constellation.core.configuration import Configuration
from constellation.core.message.cdtp2 import DataRecord
from constellation.core.monitoring import schedule_metric
from constellation.core.receiver_satellite import ReceiverSatellite

from tjmonopix2.system.scan_config_h5 import write_scan_config_to_h5
from tjmonopix2.system.scan_config import (
    ScanConfig,
    scan_config_from_payload,
    FILTER_RAW_DATA,
    FILTER_TABLES,
    RunConfigTable,
)

class MetaTable(tb.IsDescription):
    """Metadata table schema expected by downstream ScanBase analysis."""

    index_start = tb.Int64Col(pos=0)
    index_stop = tb.Int64Col(pos=1)
    data_length = tb.UInt32Col(pos=2)
    timestamp_start = tb.Float64Col(pos=3)
    timestamp_stop = tb.Float64Col(pos=4)
    scan_param_id = tb.UInt32Col(pos=5)
    error = tb.UInt32Col(pos=6)
    trigger = tb.Float64Col(pos=7)


class TJMonopix2H5Receiver(ReceiverSatellite):
    """
    Constellation satellite that writes TJ-Monopix2 data in ScanBase format.

    Responsibilities:
      - Constellation lifecycle
        (do_initializing, do_starting, do_stopping, fail_gracefully)
      - Config handling (output_directory, flush_interval)
      - Receiving BOR/EOR payloads containing serialised ScanConfig
      - Record counting and flush timing
      - Writing configuration and data using the shared ScanBase writer
    """

    def do_initializing(self, config: Configuration) -> str:
        self.output_directory = pathlib.Path(config.get_path("output_directory"))
        self.flush_interval: float = config.get_num("flush_interval", 10.0)

        self.outfile: tb.File | None = None
        self.filename: str | None = None
        self.record_counts: dict[str, int] = {}
        self._seen_eor: set[str] = set()
        self._last_flush: datetime.datetime | None = None

        return f"Initialized receiver, output directory: {self.output_directory}"

    def do_starting(self, run_identifier: str) -> str:
        self.record_counts.clear()
        self._seen_eor.clear()
        self._last_flush = datetime.datetime.now(datetime.UTC)

        filepath = self.output_directory / f"tjmonopix2_{run_identifier}.h5"
        if filepath.exists():
            raise RuntimeError(f"File already exists: {filepath}")

        self.log.info("Creating file %s", filepath)
        self.outfile = tb.open_file(
            filepath,
            mode="w",
            title="TJ-Monopix2 Constellation run",
        )
        self.filename = str(filepath)
        return f"Started run {run_identifier}"

    def do_stopping(self) -> str:
        if self.outfile is not None:
            self.outfile.flush()
            self.outfile.close()
            self.outfile = None
            self.filename = None
        return "Run stopped"

    def fail_gracefully(self) -> str:
        try:
            if self.outfile is not None:
                self.outfile.flush()
                self.outfile.close()
        except Exception:
            pass
        finally:
            self.outfile = None
            self.filename = None
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
        chip_node = self._chip_node(sender)
        bor_node = self._get_or_create_group(chip_node, "BOR", "Beginning of run")

        self._write_attributes(
            self._get_or_create_group(bor_node, "user_tags", "BOR user tags"),
            user_tags,
        )
        self._write_attributes(
            self._get_or_create_group(bor_node, "configuration", "BOR configuration"),
            configuration,
        )

        chip_payload = self._resolve_chip_payload(sender, user_tags)
        if chip_payload is None:
            self.log.warning(
                "BOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(user_tags.get("chips", {})),
            )
        else:
            cfg = scan_config_from_payload(chip_payload)
            node = self._get_or_create_group(
                chip_node, "configuration_in", "Configuration before scan"
            )
            write_scan_config_to_h5(self.outfile, node, cfg)

        self._require_data_nodes(chip_node)
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

        chip_node = self._chip_node(sender)
        eor_node = self._get_or_create_group(chip_node, "EOR", "End of run")

        self._write_attributes(
            self._get_or_create_group(eor_node, "user_tags", "EOR user tags"),
            user_tags,
        )
        self._write_attributes(
            self._get_or_create_group(eor_node, "run_metadata", "EOR run metadata"),
            run_metadata,
        )

        chip_payload = self._resolve_chip_payload(sender, user_tags)
        if chip_payload is None:
            self.log.warning(
                "EOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(user_tags.get("chips", {})),
            )
        else:
            cfg = scan_config_from_payload(chip_payload)
            node = self._get_or_create_group(
                chip_node, "configuration_out", "Configuration after scan step"
            )
            write_scan_config_to_h5(self.outfile, node, cfg)

        self._seen_eor.add(sender)
        self._maybe_flush()

    def receive_data(self, sender: str, data_record: DataRecord) -> None:
        """Append a DataRecord using ScanBase.handle_data semantics."""
        chip_node = self._chip_node(sender)
        raw_data, meta_data = self._require_data_nodes(chip_node)

        data = self._data_record_to_array(sender, data_record)
        start = raw_data.nrows
        raw_data.append(data)

        tags = data_record.tags
        row = meta_data.row
        row["index_start"] = start
        row["index_stop"] = start + len(data)
        row["data_length"] = len(data)
        row["timestamp_start"] = self._tag_number(tags, "timestamp_start", 0.0)
        row["timestamp_stop"] = self._tag_number(tags, "timestamp_stop", 0.0)
        row["scan_param_id"] = self._tag_number(tags, "scan_param_id", 0)
        row["error"] = self._tag_number(tags, "error", 0)
        row["trigger"] = self._tag_number(tags, "trigger", 0.0)
        row.append()

        raw_data.flush()
        meta_data.flush()

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
        return self.filename

    # --------------------------------------------------------------------- #
    # Helpers
    # --------------------------------------------------------------------- #

    def _maybe_flush(self) -> None:
        if self.flush_interval <= 0 or self._last_flush is None:
            return

        now = datetime.datetime.now(datetime.UTC)
        if (now - self._last_flush).total_seconds() >= self.flush_interval:
            self.outfile.flush()  # type: ignore[union-attr]
            self._last_flush = now

    def _chip_node(self, sender: str) -> tb.Group:
        if self.outfile is None:
            raise RuntimeError("Cannot receive data without an open output file")
        return self._get_or_create_group(
            self.outfile.root,
            sender,
            f"Chip {sender}",
        )

    def _get_or_create_group(
        self,
        parent: tb.Group,
        name: str,
        title: str = "",
    ) -> tb.Group:
        try:
            return parent._f_get_child(name)
        except tb.NoSuchNodeError:
            return self.outfile.create_group(parent, name, title)  # type: ignore[union-attr]

    def _require_data_nodes(
        self,
        chip_node: tb.Group,
    ) -> tuple[tb.EArray, tb.Table]:
        try:
            raw_data = chip_node.raw_data
        except tb.NoSuchNodeError:
            raw_data = self.outfile.create_earray(
                chip_node,
                "raw_data",
                atom=tb.UIntAtom(),
                shape=(0,),
                title="raw_data",
                filters=FILTER_RAW_DATA,
            )

        try:
            meta_data = chip_node.meta_data
        except tb.NoSuchNodeError:
            meta_data = self.outfile.create_table(
                chip_node,
                "meta_data",
                description=MetaTable,
                title="meta_data",
                filters=FILTER_TABLES,
            )

        return raw_data, meta_data

    def _resolve_chip_payload(
        self,
        sender: str,
        user_tags: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        """Find the chip payload (serialised ScanConfig) for this sender.

        If there is no direct match but only one chip in the BOR/EOR,
        fall back to that single chip (legacy behaviour).
        """
        chips = user_tags.get("chips", {})
        if not isinstance(chips, Mapping):
            return None

        chip_payload = chips.get(sender)
        if chip_payload is not None:
            return chip_payload

        if len(chips) == 1:
            return next(iter(chips.values()))

        return None

    @staticmethod
    def _write_attributes(
        group: tb.Group,
        values: Mapping[str, Any],
        prefix: str = "",
    ) -> None:
        for key, value in values.items():
            key = str(key)
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, Mapping):
                if value.get("__numpy__") is True:
                    if key != "data":
                        group._v_attrs[f"{full_key}.dtype"] = str(
                            value.get("dtype", "")
                        )
                        group._v_attrs[f"{full_key}.shape"] = (
                            value.get("shape", [])
                        )
                    continue
                TJMonopix2H5Receiver._write_attributes(
                    group,
                    value,
                    prefix=full_key,
                )
                continue
            group._v_attrs[full_key] = TJMonopix2H5Receiver._attribute_value(value)

    @staticmethod
    def _attribute_value(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            try:
                return value  # HDF5 can store sequences directly
            except (TypeError, ValueError):
                return str(value)
        if value is None:
            return "None"
        return value

    @staticmethod
    def _data_record_to_array(
        sender: str,
        data_record: DataRecord,
    ) -> np.ndarray:
        import numpy as np

        dtype = np.dtype(data_record.tags.get("dtype", np.uint32))
        if dtype != np.dtype(np.uint32):
            raise TypeError(
                f"Sender {sender!r} supplied {dtype}; ScanBase raw_data is uint32"
            )
        blocks = [
            np.frombuffer(block, dtype=np.uint32)
            for block in data_record.blocks
        ]
        return np.concatenate(blocks) if blocks else np.empty(0, dtype=np.uint32)

    @staticmethod
    def _tag_number(
        tags: Mapping[str, Any],
        name: str,
        default: int | float,
    ) -> int | float:
        import numpy as np

        value = tags.get(name, default)
        return value.item() if isinstance(value, np.generic) else value
