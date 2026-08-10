"""
TJ-Monopix2 Constellation receiver satellite.

    /
      receiver_metadata/
      <sender>/
        BOR/
          user_tags/                 attributes
          configuration/             flattened attributes
        data                         one appendable raw-data dataset
        meta_data/                   appendable record metadata datasets
        EOR/
          user_tags/                 attributes
          run_metadata/              flattened attributes

"""

import datetime
import json
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
    """Receiver satellite writing scan_base-style HDF5 files."""

    def do_initializing(self, config: Configuration) -> str:
        self.output_directory = config.get_path("output_directory")
        self.flush_interval = config.get_num("flush_interval", 10.0)
        self.last_flush = None
        self.outfile = None
        self.record_counts = {}
        self._seen_eor = set()
        return f"Initialized receiver, output directory: {self.output_directory}"

    def do_starting(self, run_identifier: str) -> str:
        self._seen_eor = set()
        self.last_flush = datetime.datetime.now(datetime.UTC)
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

    def receive_bor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        configuration: dict[str, Any],
    ) -> None:
        """Store the beginning-of-run metadata for a sender."""
        sender_grp = self.outfile.require_group(sender)
        bor_grp = sender_grp.require_group("BOR")

        bor_grp.require_group("user_tags").attrs.update(
            self._attrs_convert(user_tags)
        )
        bor_grp.require_group("configuration").attrs.update(
            self._attrs_convert(configuration)
        )

        self._require_data_nodes(sender_grp)

        self.record_counts.setdefault(sender, 0)

    def receive_data(self, sender: str, data_record: DataRecord) -> None:
        """Append all blocks in a DataRecord to one sender data dataset."""
        sender_grp = self.outfile.require_group(sender)
        data_dset, meta_grp = self._require_data_nodes(sender_grp)

        dtype = np.dtype(data_record.tags.get("dtype", np.uint32))
        if data_dset.dtype != dtype:
            raise TypeError(
                f"Inconsistent dtype for sender {sender!r}: "
                f"existing={data_dset.dtype}, new={dtype}"
            )

        arrays = [
            np.frombuffer(block, dtype=dtype)
            for block in data_record.blocks
        ]

        if arrays:
            new_data = np.concatenate(arrays)
            index_start = int(data_dset.shape[0])
            index_stop = index_start + int(new_data.shape[0])

            data_dset.resize((index_stop,))
            data_dset[index_start:index_stop] = new_data
        else:
            index_start = int(data_dset.shape[0])
            index_stop = index_start

        self._append_metadata(
            meta_grp,
            sequence_number=data_record.sequence_number,
            index_start=index_start,
            index_stop=index_stop,
            data_length=index_stop - index_start,
            n_blocks=len(data_record.blocks),
            tags=data_record.tags,
        )

        self.record_counts[sender] = self.record_counts.get(sender, 0) + 1
        self._flush_if_due()

    def receive_eor(
        self,
        sender: str,
        user_tags: dict[str, Any],
        run_metadata: dict[str, Any],
    ) -> None:
        """Store the end-of-run metadata for a sender."""
        if sender in self._seen_eor:
            self.log.warning(
                "Duplicate EOR received from %s, ignoring second EOR", sender
            )
            return

        sender_grp = self.outfile.require_group(sender)
        eor_grp = sender_grp.require_group("EOR")

        eor_grp.require_group("user_tags").attrs.update(
            self._attrs_convert(user_tags)
        )
        eor_grp.require_group("run_metadata").attrs.update(
            self._attrs_convert(run_metadata)
        )

        self._seen_eor.add(sender)
        self.outfile.flush()

    def _require_data_nodes(
        self,
        sender_grp: h5py.Group,
        dtype: np.dtype | None = None,
    ) -> tuple[h5py.Dataset, h5py.Group]:
        """Create or retrieve the appendable data and metadata nodes."""
        if "data" not in sender_grp:
            if dtype is None:
                dtype = np.dtype(np.uint32)

            data_dset = sender_grp.create_dataset(
                "data",
                shape=(0,),
                maxshape=(None,),
                dtype=dtype,
                chunks=True,
                compression="gzip",
                compression_opts=1,
            )
            data_dset.attrs["dtype"] = str(dtype)
            data_dset.attrs["title"] = "Raw data"
        else:
            data_dset = sender_grp["data"]

        if "meta_data" not in sender_grp:
            meta_grp = sender_grp.create_group("meta_data")
            meta_grp.attrs["title"] = "DataRecord metadata"

            self._create_appendable_dataset(
                meta_grp, "sequence_number", np.uint64
            )
            self._create_appendable_dataset(
                meta_grp, "index_start", np.uint64
            )
            self._create_appendable_dataset(
                meta_grp, "index_stop", np.uint64
            )
            self._create_appendable_dataset(
                meta_grp, "data_length", np.uint64
            )
            self._create_appendable_dataset(
                meta_grp, "n_blocks", np.uint32
            )
            self._create_appendable_dataset(
                meta_grp,
                "tags",
                h5py.string_dtype(encoding="utf-8"),
            )
        else:
            meta_grp = sender_grp["meta_data"]

        return data_dset, meta_grp

    @staticmethod
    def _create_appendable_dataset(
        group: h5py.Group,
        name: str,
        dtype: Any,
    ) -> h5py.Dataset:
        return group.create_dataset(
            name,
            shape=(0,),
            maxshape=(None,),
            dtype=dtype,
            chunks=True,
            compression="gzip",
            compression_opts=1,
        )

    def _append_metadata(
        self,
        meta_grp: h5py.Group,
        sequence_number: int,
        index_start: int,
        index_stop: int,
        data_length: int,
        n_blocks: int,
        tags: dict[str, Any],
    ) -> None:
        values = {
            "sequence_number": sequence_number,
            "index_start": index_start,
            "index_stop": index_stop,
            "data_length": data_length,
            "n_blocks": n_blocks,
            "tags": json.dumps(self._json_convert(tags), sort_keys=True),
        }

        row_index = meta_grp["sequence_number"].shape[0]
        for name, value in values.items():
            dset = meta_grp[name]
            dset.resize((row_index + 1,))
            dset[row_index] = value

    def _flush_if_due(self) -> None:
        if self.flush_interval <= 0:
            return

        now = datetime.datetime.now(datetime.UTC)
        elapsed = (now - self.last_flush).total_seconds()
        if elapsed > self.flush_interval:
            self.outfile.flush()
            self.last_flush = now

    def _open_file(self, filename: str) -> h5py.File:
        self.log.info("Creating file %s", filename)

        directory = pathlib.Path(self.output_directory)
        try:
            os.makedirs(directory, exist_ok=True)
        except Exception as exc:
            raise RuntimeError(
                f"Unable to create directory {directory}: {exc}"
            ) from exc

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
        outfile.require_group("receiver_metadata").attrs.update(
            self._attrs_convert(metadata)
        )

    def _attrs_convert(
        self,
        meta: dict[str, Any],
        _prefix: str = "",
    ) -> dict[str, Any]:
        result = {}

        for key, value in meta.items():
            full_key = f"{_prefix}.{key}" if _prefix else key
            if isinstance(value, dict):
                result.update(self._attrs_convert(value, _prefix=full_key))
            else:
                converted = self._convert_attr_value(value)
                if converted is not None:
                    result[full_key] = converted

        return result

    @staticmethod
    def _convert_attr_value(value: Any) -> Any:
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, (list, tuple)):
            try:
                return np.asarray(value)
            except (ValueError, TypeError):
                return str(value)
        if value is None:
            return "None"
        return value

    @classmethod
    def _json_convert(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(k): cls._json_convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_convert(v) for v in value]
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

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
