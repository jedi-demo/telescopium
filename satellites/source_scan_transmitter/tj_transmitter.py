from constellation.core.configuration import Configuration
from constellation.core.protocol.cscp1 import SatelliteState
from constellation.core.message.cdtp2 import DataRecord
from constellation.core.monitoring import schedule_metric
from constellation.core.transmitter_satellite import TransmitterSatellite

import os
import sys
import threading
import time
from itertools import count
from typing import Any
from queue import Full

import yaml

from tjmonopix2.scans.scan_source import SourceScan
from tjmonopix2.system.fifo_readout import FifoReadout, NoDataTimeout

class CustomFifoReadout(FifoReadout):
    """Read FIFO and forward only to Constellation DataRecords."""
    def __init__(self, daq, send_record=None, record_tag_factory=None):
        super().__init__(daq)
        self.send_record = send_record
        self.record_tag_factory = record_tag_factory
        self._sequence_counter = count()

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

    def _make_data_record(self, data, timestamp_begin, timestamp_end, status):
        seq = next(self._sequence_counter)
        record = DataRecord(
            sequence_number=seq,
            tags=self._make_tags(timestamp_begin, timestamp_end, status, int(data.shape[0])),
        )
        record.add_block(data.tobytes())
        return record

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
            self.watchdog_thread = threading.Thread(target=self.watchdog, name="WatchdogThread")
            self.watchdog_thread.daemon = True
            self.watchdog_thread.start()

        self.readout_thread = threading.Thread(
            target=self.readout,
            name="ReadoutThread",
            kwargs={"no_data_timeout": no_data_timeout},
        )
        self.readout_thread.daemon = True
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

                if self.send_record is not None:
                    record = self._make_data_record(
                        data=data,
                        timestamp_begin=timestamp_begin,
                        timestamp_end=timestamp_end,
                        status=status,
                    )
                    self.send_record(record)
       
                    while not self.stop_readout.is_set() and not self.force_stop.is_set():
                        try:
                            self.constellation_data_queue.put(record, timeout=0.1)
                            break
                        except Full:
                            continue

                self._words_per_read.append(n_words)

            finally:
                time_wait = self.readout_interval - (time.time() - time_read)

            if self._calculate_word_rate.is_set():
                self._calculate_word_rate.clear()
                self._word_rate_result.put(sum(self._words_per_read))

        self.log.debug("Stopped %s", self.readout_thread.name)

class CustomSourceScan(SourceScan):
    def __init__(self, *args, constellation_data_queue=None, record_tag_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.constellation_data_queue = constellation_data_queue
        self.record_tag_factory = record_tag_factory

    def _configure_fifo_readout(self):
        self.fifo_readout = CustomFifoReadout(
            self.daq,
            send_record=self.constellation_data_queue,
            record_tag_factory=self.record_tag_factory,
        )
        self._first_read = False


class TJMonopix2(TransmitterSatellite):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.src_scan = None
        self.constellation_data_queue = getattr(self, "data_queue", None)

    def _record_tag_factory(self, timestamp_begin, timestamp_end, status, n_words):
        return {
            "dtype": "uint32",
            "timestamp_begin": timestamp_begin,
            "timestamp_end": timestamp_end,
            "error": status,
            "n_words": n_words,
            "trigger_mode": self.scan_configuration.get("trigger_mode"),
            "chip_sn": self.bench_conf["modules"]["module_0"]["chip_0"].get("chip_sn"),
        }

    def _make_scan(self):
        return CustomSourceScan(
            scan_config=self.scan_configuration,
            bench_config=self.bench_conf,
            constellation_data_queue=self.constellation_data_queue,
            record_tag_factory=self._record_tag_factory,
        )

    def do_initializing(self, config: Configuration) -> str:
        try:
            if self.src_scan is not None:
                self.src_scan.close()
        except AttributeError:
            pass

        self._load_config(config)
        self.src_scan = self._make_scan()
        return "initializing done"

    # def do_launching(self) -> str:
    #     time.sleep(10)
    #     self.src_scan.init()
    #     self.src_scan._init_environment()
    #     self.src_scan._init_hardware(force=False)
    #     self.src_scan.initialized = True
    #     self.src_scan.configure()
    #     return "launching done"
    def do_launching(self) -> str:
        self.log.info("do_launching: enter")
        time.sleep(30)
        try:
            if self.src_scan is None:
                raise RuntimeError("src_scan is None before launch")

            self.log.info("do_launching: src_scan=%r", self.src_scan)

            self.log.info("do_launching: calling init()")
            self.src_scan.init()
            self.log.info("do_launching: init() done")

            self.log.info("do_launching: calling _init_environment()")
            self.src_scan._init_environment()
            self.log.info("_init_environment() done")

            self.log.info("do_launching: calling _init_hardware(force=False)")
            self.src_scan._init_hardware(force=False)
            self.log.info("_init_hardware() done")

            self.log.info("do_launching: setting initialized=True")
            self.src_scan.initialized = True

            self.log.info("do_launching: calling configure()")
            self.src_scan.configure()
            self.log.info("do_launching: configure() done")

            return "launching done"

        except Exception:
            self.log.exception("do_launching failed")
            raise

    def do_run(self, payload=None) -> str:
        self.src_scan._init_files()

        if hasattr(self.src_scan, "stop_scan"):
            self.src_scan.stop_scan.clear()

        self.thread_scan = threading.Thread(
            target=self.src_scan.scan,
            name="TJMonopix2ScanThread",
        )
        self.thread_scan.start()

        try:
            while not self._state_thread_evt.is_set():
                time.sleep(0.2)
        finally:
            if hasattr(self.src_scan, "stop_scan"):
                self.src_scan.stop_scan.set()

        return "running done"

    def do_stop(self) -> str:
        self.log.info("Stopping run")
        if hasattr(self.src_scan, "stop_scan"):
            self.src_scan.stop_scan.set()
        self.log.info("here1")

        if self.src_scan.fifo_readout is not None:
            self.src_scan.fifo_readout.stop_readout.set()
            self.src_scan.fifo_readout.force_stop.set()

        self.log.info("here2")
        if self.thread_scan.is_alive():
            self.thread_scan.join(timeout=10)


        return "stopped"

    def do_reconfigure(self, config: Configuration) -> str:
        if self.src_scan is not None:
            self.src_scan.close()
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
        if self.fsm.current_state_value == SatelliteState.RUN:
            return self.src_scan.daq.get_trigger_counter()
        return None
