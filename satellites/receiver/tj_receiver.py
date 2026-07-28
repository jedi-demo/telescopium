"""
TJ-Monopix2 Constellation receiver satellite.

Receives DataRecord objects from transmitter satellites and stores
TJ-Monopix2 FIFO payloads into an HDF5 file.
"""

import datetime
import os
import pathlib
from typing import Any

import h5py
import numpy as np

from constellation.core import __version__
from constellation.core.configuration import Configuration
from constellation.core.message.cdtp2 import DataRecord
from constellation.core.monitoring import schedule_metric
from constellation.core.receiver_satellite import ReceiverSatellite


class TJMonopix2H5Receiver(ReceiverSatellite):
    """Receiver satellite writing TJ-Monopix2 DataRecords to HDF5."""

    def do_initializing(self, config: Configuration) -> str:
        self.output_directory = config.get_path("output_directory")
        self.flush_interval = config.get_num("flush_interval", 10.0)
        self.last_flush = None
        self.outfile = None
        self.record_counts = {}
        return f"Initialized receiver, output directory: {self.output_directory}"

    def do_starting(self, run_identifier: str) -> str:
        self._seen_eor = set()
        self.last_flush = datetime.datetime.now()
        self.record_counts = {}
        self.outfile = self._open_file(f"tjmonopix2_{run_identifier}.h5")
        return f"Started run {run_identifier}"

    def do_stopping(self) -> str:
        if self.outfile is not None:
            self.outfile.flush()
            self.outfile.close()
            self.outfile = None
        return "Run stopped"

    def fail_gracefully(self) -> str:
        try:
            if self.outfile is not None:
                self.outfile.flush()
                self.outfile.close()
                self.outfile = None
        except Exception:
            pass
        return "Receiver failed gracefully"

    def receive_bor(self, sender: str, user_tags: dict[str, Any], configuration: dict[str, Any]) -> None:
        sender_grp = self.outfile.require_group(sender)
        bor_grp = sender_grp.create_group("BOR")
        bor_grp.create_group("user_tags").attrs.update(self._attrs_convert(user_tags))
        bor_grp.create_group("configuration").attrs.update(self._attrs_convert(configuration))

        if sender not in self.record_counts:
            self.record_counts[sender] = 0

    def receive_data(self, sender: str, data_record: DataRecord) -> None:
        sender_grp = self.outfile.require_group(sender)

        if sender not in self.record_counts:
            self.record_counts[sender] = 0

        data_grp = sender_grp.create_group(f"data_{data_record.sequence_number:09}")
        data_grp.attrs["sequence_number"] = data_record.sequence_number
        data_grp.attrs.update(self._attrs_convert(data_record.tags))

        dtype = np.dtype(data_record.tags.get("dtype", np.uint32))

        for block_idx, block in enumerate(data_record.blocks):
            arr = np.frombuffer(block, dtype=dtype)
            dset = data_grp.create_dataset(
                f"block_{block_idx:02}",
                data=arr,
                chunks=True,
                compression="gzip",
                compression_opts=1,
            )
            dset.attrs["dtype"] = str(dtype)
            dset.attrs["n_items"] = arr.shape[0]

        self.record_counts[sender] += 1

        if self.flush_interval > 0:
            elapsed = (datetime.datetime.now() - self.last_flush).total_seconds()
            if elapsed > self.flush_interval:
                self.outfile.flush()
                self.last_flush = datetime.datetime.now()

    def receive_eor(self, sender: str, user_tags: dict[str, Any], run_metadata: dict[str, Any]) -> None:
        if sender in self._seen_eor:
            self.log.warning("Duplicate EOR received from %s, ignoring second EOR", sender)
            return

        sender_grp = self.outfile.require_group(sender)
        eor_grp = sender_grp.require_group("EOR")
        eor_grp.require_group("user_tags").attrs.update(self._attrs_convert(user_tags))
        eor_grp.require_group("run_metadata").attrs.update(self._attrs_convert(run_metadata))

        self._seen_eor.add(sender)

    def _open_file(self, filename: str) -> h5py.File:
        self.log.info("Creating file %s", filename)

        directory = pathlib.Path(self.output_directory)
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception as exc:
            raise RuntimeError(f"Unable to create directory {directory}: {exc}") from exc

        filepath = directory / filename
        if filepath.exists():
            raise RuntimeError(f"File already exists: {filepath}")

        try:
            h5file = h5py.File(filepath, "w")
        except Exception as exc:
            raise RuntimeError(f"Unable to open {filepath}: {exc}") from exc

        self._add_metadata(h5file)
        return h5file

    def _add_metadata(self, outfile: h5py.File) -> None:
        metadata = {
            "constellation_version": __version__,
            "date_utc": datetime.datetime.now(datetime.UTC).isoformat(),
            "receiver_class": self.__class__.__name__,
        }
        grp = outfile.create_group(self.name)
        grp.attrs.update(self._attrs_convert(metadata))

    def _attrs_convert(self, meta: dict[str, Any], _prefix: str = "") -> dict[str, Any]:
        def _convert(value: Any) -> Any:
            if isinstance(value, datetime.datetime):
                return str(value)
            if isinstance(value, np.generic):
                return value.item()
            if isinstance(value, (list, tuple)):
                try:
                    return np.array(value)
                except (ValueError, TypeError):
                    return str(value)
            if value is None:
                return "None"
            return value

        result = {}
        for key, value in meta.items():
            full_key = f"{_prefix}.{key}" if _prefix else key
            if isinstance(value, dict):
                result.update(self._attrs_convert(value, _prefix=full_key))
            else:
                result[full_key] = _convert(value)
        return result

    @schedule_metric("int", 5)
    def total_records_received(self) -> int | None:
        if not self.record_counts:
            return 0
        return int(sum(self.record_counts.values()))

    @schedule_metric("filename", 5)
    def currently_open_filename(self) -> str | None:
        try:
            return self.outfile.filename
        except Exception:
            return None
