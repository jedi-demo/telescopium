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

from tjmonopix2.scans.scan_source import SourceScan
from tjmonopix2.system.fifo_readout import FifoReadout, NoDataTimeout


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


class CustomSourceScan(SourceScan):
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


class TJMonopix2(TransmitterSatellite):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.src_scan = None
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
        return CustomSourceScan(
            scan_config=self.scan_configuration,
            bench_config=self.bench_conf,
            send_array=self._send_array,
            record_tag_factory=self._record_tag_factory,
        )

    def do_initializing(self, config: Configuration) -> str:
        try:
            if self.src_scan is not None:
                self.src_scan.close()
        except Exception:
            self.log.exception("Ignoring exception while closing existing scan during initialization")

        self._load_config(config)
        self.src_scan = self._make_scan()
        return "initializing done"

    def do_launching(self) -> str:
        time.sleep(30)
        try:
            if self.src_scan is None:
                raise RuntimeError("src_scan is None before launch")

            self.src_scan.init()
            self.src_scan._init_environment()
            self.src_scan._init_hardware(force=False)
            self.src_scan.initialized = True
            self.src_scan.configure()

            return "launching done"

        except Exception:
            self.log.exception("do_launching failed")
            raise


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

        self.src_scan._init_files()
        if hasattr(self.src_scan, "stop_scan"):
            self.src_scan.stop_scan.clear()

        self.thread_scan = threading.Thread(
            target=self.src_scan.scan,
            name="TJMonopix2ScanThread",
        )
        self.thread_scan.start()

        try:
            while not self.stop_requested():
                time.sleep(0.2)
        finally:
            if hasattr(self.src_scan, "stop_scan"):
                self.src_scan.stop_scan.set()

        return "running done"

    def do_stop(self) -> str:
        if hasattr(self.src_scan, "stop_scan"):
            self.src_scan.stop_scan.set()

        if getattr(self.src_scan, "fifo_readout", None) is not None:
            self.src_scan.fifo_readout.stop_readout.set()
            self.src_scan.fifo_readout.force_stop.set()

        if self.thread_scan is not None and self.thread_scan.is_alive():
            self.thread_scan.join(timeout=10)


    def do_reconfigure(self, config: Configuration) -> str:
        if self.src_scan is not None:
            try:
                self.src_scan.close()
            except Exception:
                self.log.exception("Ignoring exception while closing existing scan during reconfigure")

        self._load_config(config)
        self.src_scan = self._make_scan()
        self.src_scan.init()
        return "reconfiguring done"

    def _load_config(self, config: Configuration) -> None:
        config.set_default(key="tot_calib_file", value=None)
        config.set_default(key="output_directory", value=None)
        config.set_default(key="chip_config_file", value=None)
        config.set_default(
            key="testbench_path",
            value=os.path.join(os.path.dirname(__file__), "../..", "testbench.yaml"),
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

    @schedule_metric("", 1)
    def trigger_number(self) -> Any:
        if self.fsm.current_state_value == SatelliteState.RUN and self.src_scan is not None:
            return self.src_scan.daq.get_trigger_counter()
        return None
