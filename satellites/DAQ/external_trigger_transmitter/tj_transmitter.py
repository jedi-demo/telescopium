from constellation.core.configuration import Configuration
from constellation.core.protocol.cscp1 import SatelliteState
from constellation.core.monitoring import schedule_metric
from constellation.core.transmitter_satellite import TransmitterSatellite

import os
import sys
import threading
import time
from typing import Any

import numpy as np
import yaml

from tjmonopix2.scans.scan_ext_trigger import ExtTriggerScan
from tjmonopix2.system.fifo_readout import FifoReadout, NoDataTimeout
from tjmonopix2 import utils


class CustomFifoReadout(FifoReadout):
    def __init__(self, daq, send_array=None, record_tag_factory=None):
        super().__init__(daq)
        self.send_array = send_array
        self.record_tag_factory = record_tag_factory

    def _make_tags(self, timestamp_begin, timestamp_end, status, n_words):
        if self.record_tag_factory is not None:
            return self.record_tag_factory(
                timestamp_begin=timestamp_begin,
                timestamp_end=timestamp_end,
                status=status,
                n_words=n_words,
            )
        return {
            "dtype": "uint32",
            "timestamp_begin": timestamp_begin,
            "timestamp_end": timestamp_end,
            "error": status,
            "n_words": n_words,
        }

    def start(self, errback=None, reset_rx=False, reset_sram_fifo=False, no_data_timeout=None, fill_buffer=False):
        if self._is_running:
            raise RuntimeError("FIFO readout is already running.")

        self.errback = errback
        self.fill_buffer = fill_buffer

        if reset_rx:
            self.reset_rx()
        if reset_sram_fifo:
            self.reset_sram_fifo()

        self._record_count = 0
        self._words_per_read.clear()

        self.stop_readout.clear()
        self.force_stop.clear()

        if self.errback:
            self.watchdog_thread = threading.Thread(target=self.watchdog, name="WatchdogThread", daemon=True)
            self.watchdog_thread.start()

        self.readout_thread = threading.Thread(
            target=self.readout,
            name="ReadoutThread",
            kwargs={"no_data_timeout": no_data_timeout},
            daemon=True,
        )
        self.readout_thread.start()

        self._is_running = True

    def readout(self, no_data_timeout=None):
        self.log.debug("Starting %s", self.readout_thread.name)
        curr_time = self.get_float_time()
        time_wait = 0.0

        while not self.force_stop.wait(time_wait if time_wait >= 0.0 else 0.0):
            try:
                time_read = time.time()

                if no_data_timeout and curr_time + no_data_timeout < self.get_float_time():
                    raise NoDataTimeout("Received no data for %0.1f second(s)" % no_data_timeout)

                data = self.read_data()
                n_words = data.shape[0]
                self._record_count += n_words

            except Exception:
                no_data_timeout = None
                if self.errback:
                    self.errback(sys.exc_info())
                else:
                    raise
                if self.stop_readout.is_set():
                    break

            else:
                if n_words == 0:
                    if self.stop_readout.is_set():
                        break
                    continue

                timestamp_begin, timestamp_end = self.update_timestamp()
                status = 0
                tags = self._make_tags(timestamp_begin, timestamp_end, status, int(n_words))

                if self.send_array is not None:
                    self.send_array(data, tags)

                self._words_per_read.append(n_words)

            finally:
                time_wait = self.readout_interval - (time.time() - time_read)

            if self._calculate_word_rate.is_set():
                self._calculate_word_rate.clear()
                self._word_rate_result.put(sum(self._words_per_read))

        self.log.debug("Stopped %s", self.readout_thread.name)


class CustomExtTriggerScan(ExtTriggerScan):
    def __init__(self, *args, send_array=None, record_tag_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_array = send_array
        self.record_tag_factory = record_tag_factory

    def _configure_fifo_readout(self):
        self.log.info("_configure_fifo_readout: installing CustomFifoReadout")
        self.fifo_readout = CustomFifoReadout(
            self.daq,
            send_array=self.send_array,
            record_tag_factory=self.record_tag_factory,
        )
        self._first_read = False

def _make_serializable(obj):
    """Recursively convert an object into a JSON-serializable structure."""
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        # Store dtype, shape, and data as a list
        return {
            "__numpy__": True,
            "dtype": str(obj.dtype),
            "shape": list(obj.shape),
            "data": obj.tolist(),
        }
    if isinstance(obj, np.generic):
        # Scalar numpy types
        return obj.item()
    # Basic JSON-safe types
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # Fallback: coerce to string
    return str(obj)

class TJMonopix2(TransmitterSatellite):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ext_trg_scan = None
        self.thread_scan = None

    def _send_array(self, data, tags):
        if not self.can_send_record():
            self.log.warning("Cannot send record right now, dropping chunk with %d words", data.shape[0])
            return

        record = self.new_data_record(tags)
        record.add_block(data.tobytes())
        self.log.info(
            "Sending DataRecord n_words=%d dtype=%s source=%s",
            int(data.shape[0]),
            tags.get("dtype"),
            tags.get("source", "daq"),
        )
        self.send_data_record(record)

    def _record_tag_factory(self, timestamp_begin, timestamp_end, status, n_words):
        return {
            "dtype": "uint32",
            "timestamp_begin": timestamp_begin,
            "timestamp_end": timestamp_end,
            "error": status,
            "n_words": n_words,
            "trigger_mode": self.scan_configuration.get("trigger_mode"),
            "chip_sn": self.bench_conf["modules"]["module_0"]["chip_0"].get("chip_sn"),
            "source": "daq",
        }

    def _make_scan(self):
        return CustomExtTriggerScan(
            scan_config=self.scan_configuration,
            bench_config=self.bench_conf,
            send_array=self._send_array,
            record_tag_factory=self._record_tag_factory,
        )

    def do_initializing(self, config: Configuration) -> str:
        try:
            if self.ext_trg_scan is not None:
                self.ext_trg_scan.close()
        except Exception:
            self.log.exception("Ignoring exception while closing existing scan during initialization")

        self._load_config(config)
        self.ext_trg_scan = self._make_scan()
        return "initializing done"

    def do_launching(self) -> str:
        try:
            if self.ext_trg_scan is None:
                raise RuntimeError("ext_trg_scan is None before launch")

            self.ext_trg_scan.init()
            self.ext_trg_scan._init_environment()
            self.ext_trg_scan._init_hardware(force=False)
            self.ext_trg_scan.initialized = True
            self.ext_trg_scan.configure()

            return "launching done"

        except Exception:
            self.log.exception("do_launching failed")
            raise

    def do_starting(self, run_identifier: str) -> str:
        self.bor = self._build_meta_payload("in")
        self.log.info(self.bor)
        return "Started"

    def do_stopping(self) -> str:
        self.eor = self._build_meta_payload("out")
        return "Stopping"

    def send_test_packets(self, payload=None) -> str:
        self.log.info("synthetic Constellation send test start")

        for i in range(5):
            if self.stop_requested():
                break

            data = np.array([i, i + 1, i + 2, i + 3], dtype=np.uint32)
            tags = {
                "dtype": "uint32",
                "timestamp_begin": time.time(),
                "timestamp_end": time.time(),
                "error": 0,
                "n_words": int(data.size),
                "source": "synthetic-test",
            }

            self.log.info("sending synthetic test record %d", i)
            self._send_array(data, tags)
            time.sleep(1)

        self.log.info("finished synthetic send loop")
        return "Finished test"

    def do_run(self, payload=None) -> str:
        self.send_test_packets()

        self.ext_trg_scan._init_files()
        if hasattr(self.ext_trg_scan, "stop_scan"):
            self.ext_trg_scan.stop_scan.clear()

        self.thread_scan = threading.Thread(
            target=self.ext_trg_scan.scan,
            name="TJMonopix2ScanThread",
        )
        self.thread_scan.start()

        try:
            while not self.stop_requested():
                time.sleep(0.2)
        finally:
            if hasattr(self.ext_trg_scan, "stop_scan"):
                self.ext_trg_scan.stop_scan.set()

        return "running done"

    def do_stop(self) -> str:
        if hasattr(self.ext_trg_scan, "stop_scan"):
            self.ext_trg_scan.stop_scan.set()

        if getattr(self.ext_trg_scan, "fifo_readout", None) is not None:
            self.ext_trg_scan.fifo_readout.stop_readout.set()
            self.ext_trg_scan.fifo_readout.force_stop.set()

        if self.thread_scan is not None and self.thread_scan.is_alive():
            self.thread_scan.join(timeout=10)


    def do_reconfigure(self, config: Configuration) -> str:
        if self.ext_trg_scan is not None:
            try:
                self.ext_trg_scan.close()
            except Exception:
                self.log.exception("Ignoring exception while closing existing scan during reconfigure")

        self._load_config(config)
        self.ext_trg_scan = self._make_scan()
        self.ext_trg_scan.init()
        return "reconfiguring done"

    def _load_config(self, config: Configuration) -> None:
        config.set_default(key="tot_calib_file", value=None)
        config.set_default(key="output_directory", value=None)
        config.set_default(key="chip_config_file", value=None)
        config.set_default(
            key="testbench_path",
            value=os.path.join(os.path.dirname(__file__), "../../..", "testbench.yaml"),
        )
        config.set_default(key="scan_timeout", value=False)
        config.set_default(key="send_data", value="tcp://127.0.0.1:5500")
        config.set_default(key="trigger_mode", value="eudet")
        config.set_default(key="create_pdf", value=True)

        self.scan_configuration = {
            "start_column": config.get_int(key="start_column"),
            "stop_column": config.get_int(key="stop_column"),
            "start_row": config.get_int(key="start_row"),
            "stop_row": config.get_int(key="stop_row"),
            "scan_timeout": config.get_int(key="scan_timeout"),
            "max_triggers": config.get_int(key="max_triggers"),
            "tot_calib_file": config.get(key="tot_calib_file"),
            "trigger_mode": config.get("trigger_mode"),
        }

        with open(config.get_path(key="testbench_path", check_exists=True), "r") as f:
            self.bench_conf = yaml.full_load(f)
            self.bench_conf["general"]["output_directory"] = config.get(key="output_directory")
            self.bench_conf["modules"]["module_0"]["chip_0"]["chip_config_file"] = config.get("chip_config_file")
            self.bench_conf["modules"]["module_0"]["chip_0"]["chip_sn"] = config.get("chip_sn")
            self.bench_conf["modules"]["module_0"]["chip_0"]["send_data"] = config.get("send_data")
            self.bench_conf["analysis"]["create_pdf"] = config.get("create_pdf")

    def _build_meta_payload(self, stage: str) -> dict:
        """Build BOR payload containing configuration_in for each chip."""
        assert stage in ["in", "out"]
        chips_config = {}

        for name, container in self.ext_trg_scan.chips.items():
            # Activate this chip's handles on the scan so we can read
            # self.ext_trg_scan.chip, self.ext_trg_scan.chip_settings, etc.
            self.ext_trg_scan._set_chip_handles(container)

            chips_config[name] = self._extract_chip_configuration(
                container=container,
                stage=stage,
            )
        self.ext_trg_scan._unset_chip_handles()

        raw_payload = {
            "chips": chips_config,
            "run_identifier": getattr(self.ext_trg_scan, "run_name", None),
        }
        return _make_serializable(raw_payload)

    def _extract_chip_configuration(
        self,
        container,
        stage: str,
    ) -> dict:
        """Extract a ScanBase-compatible configuration dict for one chip."""
        scan = self.ext_trg_scan

        # Basic run metadata
        scan_id = getattr(scan, "scan_id", "")
        run_name = getattr(scan, "run_name", "")
        software_version = self._get_software_version()

        # Chip-level settings already present on the container / scan
        chip_settings = dict(container.chip_settings)
        module_settings = dict(container.module_settings)
        scan_config = dict(container.scan_config)

        # Registers and masks come from the live chip object
        chip = scan.chip
        registers = {
            name: str(reg.get())
            for name, reg in chip.registers.items()
        }

        masks = {
            name: mask.copy()
            for name, mask in chip.masks.items()
        }

        use_pixel = getattr(chip.masks, "disable_mask", None)
        if use_pixel is not None:
            use_pixel = use_pixel.copy()

        # Bench configuration is stored on the scan
        bench_configuration = dict(scan.configuration.get("bench", {}))

        # Assemble into the same logical structure that ScanBase writes:
        return {
            "scan": {
                "run_config": {
                    "scan_id": scan_id,
                    "run_name": run_name,
                    "software_version": software_version,
                    "module": module_settings.get("name", ""),
                    "chip_sn": chip_settings.get("chip_sn", container.name),
                    "receiver": chip_settings.get("receiver", ""),
                },
                "scan_config": {
                    k: v
                    for k, v in scan_config.items()
                    if k not in ("chip",)
                },
            },
            "chip": {
                "registers": registers,
                "settings": chip_settings,
                "module": module_settings,
                "masks": masks,
                "use_pixel": use_pixel,
            },
            "bench": bench_configuration,
        }

    @staticmethod
    def _get_software_version() -> str:
        try:
            return utils.get_software_version()
        except Exception:
            return ""

    @schedule_metric("", 1)
    def trigger_number(self) -> Any:
        if self.fsm.current_state_value == SatelliteState.RUN and self.ext_trg_scan is not None:
            return self.ext_trg_scan.daq.get_trigger_counter()
        return None
