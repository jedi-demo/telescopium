"""ScanBase-compatible HDF5 writing for the Constellation receiver.

The configuration writer intentionally follows ``ScanBase.write_config_h5``
closely. The HDF5 layout is part of the ScanBase compatibility contract.
"""

from __future__ import annotations

import datetime
import os
import pathlib
from collections.abc import Mapping
from typing import Any

import numpy as np
import tables as tb

from constellation.core.message.cdtp2 import DataRecord

# Copied from tjmonopix2.scan_base.
FILTER_RAW_DATA = tb.Filters(complib="blosc", complevel=5, fletcher32=False)
FILTER_TABLES = tb.Filters(complib="zlib", complevel=5, fletcher32=False)

# Copied from tjmonopix2.scan_base.
class MetaTable(tb.IsDescription):
    index_start = tb.Int64Col(pos=0)
    index_stop = tb.Int64Col(pos=1)
    data_length = tb.UInt32Col(pos=2)
    timestamp_start = tb.Float64Col(pos=3)
    timestamp_stop = tb.Float64Col(pos=4)
    scan_param_id = tb.UInt32Col(pos=5)
    error = tb.UInt32Col(pos=6)
    trigger = tb.Float64Col(pos=7)


class RunConfigTable(tb.IsDescription):
    attribute = tb.StringCol(64)
    value = tb.StringCol(512)


class RegisterTable(tb.IsDescription):
    register = tb.StringCol(64)
    value = tb.StringCol(256)


class ScanBaseH5Writer:
    """Write one TJ-Monopix2 run in the ScanBase HDF5 layout."""

    def __init__(self) -> None:
        self.outfile: tb.File | None = None
        self.filename: str | None = None

    def open_run(
        self,
        directory: pathlib.Path,
        run_identifier: str,
        log: Any | None = None,
    ) -> None:
        os.makedirs(directory, exist_ok=True)
        filepath = directory / f"tjmonopix2_{run_identifier}.h5"
        if filepath.exists():
            raise RuntimeError(f"File already exists: {filepath}")

        if log is not None:
            log.info("Creating file %s", filepath)

        try:
            self.outfile = tb.open_file(
                filepath,
                mode="w",
                title="TJ-Monopix2 Constellation run",
            )
        except OSError as exc:
            raise RuntimeError(f"Unable to open {filepath}: {exc}") from exc

        self.filename = str(filepath)

    def close(self) -> None:
        if self.outfile is not None:
            self.outfile.flush()
            self.outfile.close()
            self.outfile = None
            self.filename = None

    def flush(self) -> None:
        if self.outfile is not None:
            self.outfile.flush()

    def write_bor(
        self,
        sender: str,
        user_tags: Mapping[str, Any],
        configuration: Mapping[str, Any],
        chip_config: Mapping[str, Any] | None,
    ) -> None:
        """Write BOR metadata and configuration before the scan."""
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

        if chip_config is not None:
            node = self._get_or_create_group(
                chip_node, "configuration_in", "Configuration before scan"
            )
            self.write_config_h5(node, chip_config)

        self._require_data_nodes(chip_node)

    def write_eor(
        self,
        sender: str,
        user_tags: Mapping[str, Any],
        run_metadata: Mapping[str, Any],
        chip_config: Mapping[str, Any] | None,
    ) -> None:
        """Write EOR metadata and configuration after the scan."""
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

        if chip_config is not None:
            node = self._get_or_create_group(
                chip_node, "configuration_out", "Configuration after scan step"
            )
            self.write_config_h5(node, chip_config)

    def append_record(self, sender: str, data_record: DataRecord) -> None:
        """Append one record using ScanBase raw-data semantics."""
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

    def write_config_h5(
        self,
        node: tb.Group,
        configuration: Mapping[str, Any],
    ) -> None:
        """Write configuration using the structure of ScanBase.write_config_h5."""
        file = self._require_file()

        scan_node = file.create_group(node, "scan", "Scan configuration")
        self._write_dict_to_table(
            configuration.get("scan", {}),
            file.create_table(
                scan_node,
                "scan_config",
                description=RunConfigTable,
                title="Scan configuration",
                filters=FILTER_TABLES,
            ),
        )

        chip_node = file.create_group(node, "chip", "Chip configuration")
        chip = configuration.get("chip", {})

        self._write_dict_to_table(
            chip.get("settings", {}),
            file.create_table(
                chip_node,
                "settings",
                description=RunConfigTable,
                title="Chip settings from test bench",
                filters=FILTER_TABLES,
            ),
        )
        self._write_dict_to_table(
            chip.get("module", {}),
            file.create_table(
                chip_node,
                "module",
                description=RunConfigTable,
                title="Module settings from test bench",
                filters=FILTER_TABLES,
            ),
        )

        registers = file.create_table(
            chip_node,
            "registers",
            description=RegisterTable,
            title="Registers",
            filters=FILTER_TABLES,
        )
        for name, register in self._mapping(chip.get("registers", {})).items():
            row = registers.row
            row["register"] = name
            row["value"] = register
            row.append()
        registers.flush()

        masks = chip.get("masks", {})
        if masks:
            masks_node = file.create_group(chip_node, "masks", "Pixel masks")
            for name, value in masks.items():
                arr = self._decode_array(value)
                file.create_carray(
                    masks_node,
                    name=str(name),
                    atom=tb.Atom.from_dtype(arr.dtype),
                    title=str(name).capitalize(),
                    obj=arr,
                    filters=FILTER_RAW_DATA,
                )

        if chip.get("use_pixel") is not None:
            arr = self._decode_array(chip["use_pixel"])
            file.create_carray(
                chip_node,
                "use_pixel",
                atom=tb.Atom.from_dtype(arr.dtype),
                title="Select pixels to be used in scans",
                obj=arr,
                filters=FILTER_RAW_DATA,
            )

        bench_node = file.create_group(node, "bench", "Test bench settings")
        for name, values in self._mapping(configuration.get("bench", {})).items():
            self._write_dict_to_table(
                values,
                file.create_table(
                    bench_node,
                    str(name),
                    description=RunConfigTable,
                    title=str(name).capitalize(),
                    filters=FILTER_TABLES,
                ),
            )

    def _chip_node(self, sender: str) -> tb.Group:
        file = self._require_file()
        return self._get_or_create_group(file.root, sender, f"Chip {sender}")

    def _require_file(self) -> tb.File:
        if self.outfile is None:
            raise RuntimeError("No HDF5 file is open; call open_run() first")
        return self.outfile

    def _get_or_create_group(
        self,
        parent: tb.Group,
        name: str,
        title: str = "",
    ) -> tb.Group:
        try:
            return parent._f_get_child(name)
        except tb.NoSuchNodeError:
            return self._require_file().create_group(parent, name, title)

    def _require_data_nodes(
        self,
        chip_node: tb.Group,
    ) -> tuple[tb.EArray, tb.Table]:
        file = self._require_file()
        try:
            raw_data = chip_node.raw_data
        except tb.NoSuchNodeError:
            raw_data = file.create_earray(
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
            meta_data = file.create_table(
                chip_node,
                "meta_data",
                description=MetaTable,
                title="meta_data",
                filters=FILTER_TABLES,
            )

        return raw_data, meta_data

    def _write_attributes(
        self,
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
                        group._v_attrs[f"{full_key}.dtype"] = str(value.get("dtype", ""))
                        group._v_attrs[f"{full_key}.shape"] = np.asarray(
                            value.get("shape", []), dtype=np.int64
                        )
                else:
                    self._write_attributes(group, value, full_key)
            else:
                group._v_attrs[full_key] = self._attribute_value(value)

    @staticmethod
    def _write_dict_to_table(values: Any, table: tb.Table) -> None:
        for attribute, value in ScanBaseH5Writer._mapping(values).items():
            row = table.row
            row["attribute"] = attribute
            row["value"] = str(value)
            row.append()
        table.flush()

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

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
        return "None" if value is None else value

    @staticmethod
    def _decode_array(value: Any) -> np.ndarray:
        if isinstance(value, Mapping) and value.get("__numpy__") is True:
            array = np.asarray(value["data"], dtype=np.dtype(value["dtype"]))
            return array.reshape(tuple(value["shape"]))
        return np.asarray(value)

    @staticmethod
    def _data_record_to_array(sender: str, data_record: DataRecord) -> np.ndarray:
        dtype = np.dtype(data_record.tags.get("dtype", np.uint32))
        if dtype != np.dtype(np.uint32):
            raise TypeError(
                f"Sender {sender!r} supplied {dtype}; ScanBase raw_data is uint32"
            )
        blocks = [np.frombuffer(block, dtype=np.uint32) for block in data_record.blocks]
        return np.concatenate(blocks) if blocks else np.empty(0, dtype=np.uint32)

    @staticmethod
    def _tag_number(
        tags: Mapping[str, Any],
        name: str,
        default: int | float,
    ) -> int | float:
        value = tags.get(name, default)
        return value.item() if isinstance(value, np.generic) else value