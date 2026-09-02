"""TJ-Monopix2 Constellation receiver writing ScanBase-compatible HDF5 files.

Each sender is treated as one chip. Consequently, an output file has this
layout::

    /<sender>/
        configuration_in/
        raw_data
        meta_data
        configuration_out/

The contents of every ``/<sender>`` group use the same node names, PyTables
filters, raw-data EArray, and metadata table schema as ``ScanBase``.
"""

import datetime
import os
import pathlib
from collections.abc import Mapping
from typing import Any

import numpy as np
import tables as tb

from constellation.core import __version__
from constellation.core.configuration import Configuration
from constellation.core.message.cdtp2 import DataRecord
from constellation.core.monitoring import schedule_metric
from constellation.core.receiver_satellite import ReceiverSatellite


# Copied from tjmonopix2.scan_base.
FILTER_RAW_DATA = tb.Filters(complib="blosc", complevel=5, fletcher32=False)
FILTER_TABLES = tb.Filters(complib="zlib", complevel=5, fletcher32=False)


# Copied from tjmonopix2.scan_base.
# downstream scan analysis expects this exact metadata-table layout.
class MetaTable(tb.IsDescription):
    index_start = tb.Int64Col(pos=0)
    index_stop = tb.Int64Col(pos=1)
    data_length = tb.UInt32Col(pos=2)
    timestamp_start = tb.Float64Col(pos=3)
    timestamp_stop = tb.Float64Col(pos=4)
    scan_param_id = tb.UInt32Col(pos=5)
    error = tb.UInt32Col(pos=6)
    trigger = tb.Float64Col(pos=7)


# Copied from tjmonopix2.scan_base.
class RunConfigTable(tb.IsDescription):
    attribute = tb.StringCol(64)
    value = tb.StringCol(512)


# Copied from tjmonopix2.scan_base.
class RegisterTable(tb.IsDescription):
    register = tb.StringCol(64)
    value = tb.StringCol(256)


class TJMonopix2H5Receiver(ReceiverSatellite):
    """Write one ScanBase-format hierarchy per data sender/chip."""

    def do_initializing(self, config: Configuration) -> str:
        self.output_directory = config.get_path("output_directory")
        self.flush_interval = config.get_num("flush_interval", 10.0)
        self.last_flush: datetime.datetime | None = None
        self.outfile: tb.File | None = None
        self.run_identifier: str | None = None
        self.record_counts: dict[str, int] = {}
        self._seen_eor: set[str] = set()
        return f"Initialized receiver, output directory: {self.output_directory}"

    def do_starting(self, run_identifier: str) -> str:
        self._seen_eor = set()
        self.record_counts = {}
        self.run_identifier = run_identifier
        self.last_flush = datetime.datetime.now(datetime.UTC)
        self.outfile = self._open_file(f"tjmonopix2_{run_identifier}.h5")
        return f"Started run {run_identifier}"

    def do_stopping(self) -> str:
        self._close_file()
        return "Run stopped"

    def fail_gracefully(self) -> str:
        try:
            self._close_file()
        except Exception:
            pass
        return "Receiver failed gracefully"

    def receive_bor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        configuration: dict[str, Any],
    ) -> None:
        """Store BOR metadata and ScanBase-style configuration_in."""
        sender_group = self._chip_group(sender)

        bor_group = self._get_or_create_group(
            sender_group,
            "BOR",
            "Beginning of run",
        )

        bor_user_tags = self._get_or_create_group(
            bor_group,
            "user_tags",
            "BOR user tags",
        )
        self._write_attributes(bor_user_tags, user_tags)

        bor_configuration = self._get_or_create_group(
            bor_group,
            "configuration",
            "BOR configuration",
        )
        self._write_attributes(bor_configuration, configuration)

        chips = user_tags.get("chips", {})
        chip_config = chips.get(sender)

        if chip_config is None and len(chips) == 1:
            chip_config = next(iter(chips.values()))

        if chip_config is None:
            self.log.warning(
                "BOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(chips),
            )
        else:
            configuration_in = self._get_or_create_group(
                sender_group,
                "configuration_in",
                "Configuration before scan",
            )
            self._write_configuration_tree(configuration_in, chip_config)

        self._require_data_nodes(sender_group)
        self.record_counts.setdefault(sender, 0)
        self.outfile.flush()

    def receive_eor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        run_metadata: dict[str, Any],
    ) -> None:
        """Store EOR metadata and ScanBase-style configuration_out."""
        if sender in self._seen_eor:
            self.log.warning(
                "Duplicate EOR received from %s; ignoring it",
                sender,
            )
            return

        sender_group = self._chip_group(sender)

        eor_group = self._get_or_create_group(
            sender_group,
            "EOR",
            "End of run",
        )

        eor_user_tags = self._get_or_create_group(
            eor_group,
            "user_tags",
            "EOR user tags",
        )
        self._write_attributes(eor_user_tags, user_tags)

        eor_run_metadata = self._get_or_create_group(
            eor_group,
            "run_metadata",
            "EOR run metadata",
        )
        self._write_attributes(eor_run_metadata, run_metadata)

        chips = user_tags.get("chips", {})
        chip_config = chips.get(sender)

        if chip_config is None and len(chips) == 1:
            chip_config = next(iter(chips.values()))

        if chip_config is None:
            self.log.warning(
                "EOR from %s contains no matching chip configuration; "
                "available chips: %s",
                sender,
                list(chips),
            )
        else:
            configuration_out = self._get_or_create_group(
                sender_group,
                "configuration_out",
                "Configuration after scan step",
            )
            self._write_configuration_tree(configuration_out, chip_config)

        self._seen_eor.add(sender)
        self.outfile.flush()

    def receive_data(self, sender: str, data_record: DataRecord) -> None:
        """Append a DataRecord using ScanBase.handle_data semantics."""
        chip_group = self._chip_group(sender)
        raw_data, meta_data = self._require_data_nodes(chip_group)

        data = self._data_record_to_array(sender, data_record)
        total_words = raw_data.nrows
        raw_data.append(data)

        tags = data_record.tags
        row = meta_data.row
        row["timestamp_start"] = self._tag_number(tags, "timestamp_start", 0.0)
        row["timestamp_stop"] = self._tag_number(tags, "timestamp_stop", 0.0)
        row["error"] = self._tag_number(tags, "error", 0)
        row["data_length"] = data.shape[0]
        row["index_start"] = total_words
        row["index_stop"] = total_words + data.shape[0]
        row["scan_param_id"] = self._tag_number(tags, "scan_param_id", 0)
        row["trigger"] = self._tag_number(tags, "trigger", 0.0)
        row.append()

        raw_data.flush()
        meta_data.flush()

        self.record_counts[sender] = self.record_counts.get(sender, 0) + 1
        self._flush_if_due()

    def _require_data_nodes(
        self,
        chip_group: tb.Group,
    ) -> tuple[tb.EArray, tb.Table]:
        """Create/retrieve the exact raw-data and metadata nodes from ScanBase."""
        try:
            raw_data = chip_group.raw_data
        except tb.NoSuchNodeError:
            raw_data = self.outfile.create_earray(
                chip_group,
                name="raw_data",
                atom=tb.UIntAtom(),
                shape=(0,),
                title="raw_data",
                filters=FILTER_RAW_DATA,
            )

        try:
            meta_data = chip_group.meta_data
        except tb.NoSuchNodeError:
            meta_data = self.outfile.create_table(
                chip_group,
                name="meta_data",
                description=MetaTable,
                title="meta_data",
                filters=FILTER_TABLES,
            )

        return raw_data, meta_data

    def _write_configuration_tree(
        self,
        parent: tb.Group,
        config: Mapping[str, Any],
    ) -> None:
        """Write a ScanBase-style configuration subtree using PyTables."""
        if "scan" in config:
            self._write_scan_config(parent, config["scan"])

        if "chip" in config:
            self._write_chip_config(parent, config["chip"])

        if "bench" in config:
            self._write_bench_config(parent, config["bench"])

    def _write_scan_config(
        self,
        parent: tb.Group,
        scan: Mapping[str, Any],
    ) -> None:
        scan_group = self._get_or_create_group(
            parent,
            "scan",
            "Scan configuration",
        )

        run_config_table = self.outfile.create_table(
            scan_group,
            name="run_config",
            title="Run config",
            description=RunConfigTable,
            filters=FILTER_TABLES,
        )
        self._write_dict_to_table(
            scan.get("run_config", {}),
            run_config_table,
        )

        scan_config_table = self.outfile.create_table(
            scan_group,
            name="scan_config",
            title="Scan configuration",
            description=RunConfigTable,
            filters=FILTER_TABLES,
        )
        self._write_dict_to_table(
            scan.get("scan_config", {}),
            scan_config_table,
        )

    def _write_chip_config(
        self,
        parent: tb.Group,
        chip: Mapping[str, Any],
    ) -> None:
        chip_group = self._get_or_create_group(
            parent,
            "chip",
            "Chip configuration",
        )

        registers_table = self.outfile.create_table(
            chip_group,
            name="registers",
            title="Registers",
            description=RegisterTable,
            filters=FILTER_TABLES,
        )
        self._write_registers(
            chip.get("registers", {}),
            registers_table,
        )

        settings_table = self.outfile.create_table(
            chip_group,
            name="settings",
            title="Chip settings from test bench",
            description=RunConfigTable,
            filters=FILTER_TABLES,
        )
        self._write_dict_to_table(
            chip.get("settings", {}),
            settings_table,
        )

        module_table = self.outfile.create_table(
            chip_group,
            name="module",
            title="Module settings from test bench",
            description=RunConfigTable,
            filters=FILTER_TABLES,
        )
        self._write_dict_to_table(
            chip.get("module", {}),
            module_table,
        )

        masks = chip.get("masks", {})
        if masks:
            masks_group = self._get_or_create_group(
                chip_group,
                "masks",
                "Pixel masks",
            )

            for name, encoded_mask in masks.items():
                mask = self._decode_array(encoded_mask)
                self.outfile.create_carray(
                    masks_group,
                    name=str(name),
                    title=str(name).capitalize(),
                    obj=mask,
                    filters=FILTER_RAW_DATA,
                )

        if chip.get("use_pixel") is not None:
            use_pixel = self._decode_array(chip["use_pixel"])
            self.outfile.create_carray(
                chip_group,
                name="use_pixel",
                title="Select pixels to be used in scans",
                obj=use_pixel,
                filters=FILTER_RAW_DATA,
            )

    def _write_bench_config(
        self,
        parent: tb.Group,
        bench: Mapping[str, Any],
    ) -> None:
        bench_group = self._get_or_create_group(
            parent,
            "bench",
            "Test bench settings",
        )

        for section, contents in bench.items():
            sec_group = self._get_or_create_group(
                bench_group,
                str(section),
                str(section).capitalize(),
            )
            self._write_attributes(sec_group, contents or {})

    def _get_or_create_group(
        self,
        parent: tb.Group,
        name: str,
        title: str = "",
    ) -> tb.Group:
        """Return an existing PyTables group or create it."""
        try:
            child = parent._f_get_child(name)
        except tb.NoSuchNodeError:
            return self.outfile.create_group(parent, name, title)

        if not isinstance(child, tb.Group):
            raise TypeError(
                f"Expected group {parent._v_pathname}/{name}, "
                f"found {type(child).__name__}"
            )

        return child

    def _write_attributes(
        self,
        group: tb.Group,
        values: Mapping[str, Any],
        prefix: str = "",
    ) -> None:
        """Write small diagnostic values as PyTables attributes.

        Large serialized NumPy arrays are not written as attributes. They are
        written separately as CArrays in the ScanBase-compatible configuration
        tree.
        """
        for key, value in values.items():
            key = str(key)
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, Mapping):
                if value.get("__numpy__") is True:
                    if key == "data":
                        continue

                    group._v_attrs[f"{full_key}.dtype"] = str(
                        value.get("dtype", "")
                    )
                    group._v_attrs[f"{full_key}.shape"] = np.asarray(
                        value.get("shape", []),
                        dtype=np.int64,
                    )
                    continue

                self._write_attributes(
                    group,
                    value,
                    prefix=full_key,
                )
                continue

            group._v_attrs[full_key] = self._attribute_value(value)

    @staticmethod
    def _attribute_value(value: Any) -> Any:
        if isinstance(value, np.generic):
            return value.item()

        if isinstance(value, datetime.datetime):
            return value.isoformat()

        if isinstance(value, (list, tuple)):
            try:
                return np.asarray(value)
            except (TypeError, ValueError):
                return str(value)

        if value is None:
            return "None"

        return value

    @staticmethod
    def _decode_array(value: Any) -> np.ndarray:
        if (
            isinstance(value, Mapping)
            and value.get("__numpy__") is True
        ):
            array = np.asarray(
                value["data"],
                dtype=np.dtype(value["dtype"]),
            )
            return array.reshape(tuple(value["shape"]))

        return np.asarray(value)

    @staticmethod
    def _write_dict_to_table(values: Any, table: tb.Table) -> None:
        for attribute, value in TJMonopix2H5Receiver._mapping(values).items():
            row = table.row
            row["attribute"] = str(attribute)
            row["value"] = TJMonopix2H5Receiver._table_value(value)
            row.append()
        table.flush()

    @staticmethod
    def _write_registers(values: Any, table: tb.Table) -> None:
        for register, value in TJMonopix2H5Receiver._mapping(values).items():
            row = table.row
            row["register"] = str(register)
            row["value"] = TJMonopix2H5Receiver._table_value(value)
            row.append()
        table.flush()

    @staticmethod
    def _data_record_to_array(sender: str, data_record: DataRecord) -> np.ndarray:
        dtype = np.dtype(data_record.tags.get("dtype", np.uint32))
        if dtype != np.dtype(np.uint32):
            raise TypeError(
                f"Sender {sender!r} supplied {dtype}; ScanBase raw_data is uint32"
            )

        blocks = [np.frombuffer(block, dtype=np.uint32) for block in data_record.blocks]
        return np.concatenate(blocks) if blocks else np.empty(0, dtype=np.uint32)

    def _chip_group(self, sender: str) -> tb.Group:
        if self.outfile is None:
            raise RuntimeError("Cannot receive data without an open output file")

        return self._get_or_create_group(
            self.outfile.root,
            sender,
            f"Chip {sender}",
        )

    def _open_file(self, filename: str) -> tb.File:
        directory = pathlib.Path(self.output_directory)
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Unable to create directory {directory}: {exc}") from exc

        filepath = directory / filename
        if filepath.exists():
            raise RuntimeError(f"File already exists: {filepath}")

        self.log.info("Creating file %s", filepath)
        try:
            return tb.open_file(filepath, mode="w", title="TJ-Monopix2 Constellation run")
        except OSError as exc:
            raise RuntimeError(f"Unable to open {filepath}: {exc}") from exc

    def _close_file(self) -> None:
        if self.outfile is not None:
            self.outfile.flush()
            self.outfile.close()
            self.outfile = None

    def _flush_if_due(self) -> None:
        if self.flush_interval <= 0 or self.last_flush is None:
            return

        now = datetime.datetime.now(datetime.UTC)
        if (now - self.last_flush).total_seconds() >= self.flush_interval:
            self.outfile.flush()
            self.last_flush = now

    @schedule_metric("int", 5)
    def total_records_received(self) -> int:
        return sum(self.record_counts.values())

    @schedule_metric("filename", 5)
    def currently_open_filename(self) -> str | None:
        return self.outfile.filename if self.outfile is not None else None

    @staticmethod
    def _tag_number(tags: Mapping[str, Any], name: str, default: int | float) -> int | float:
        value = tags.get(name, default)
        return value.item() if isinstance(value, np.generic) else value

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _table_value(value: Any) -> str:
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if isinstance(value, np.generic):
            value = value.item()
        return str(value)
