#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Satellite:
    window: str
    module: str
    group: str
    name: str


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"

SESSIONS = {
    "dcs": {
        "session_name": "telescopium-dcs",
        "log_subdir": "dcs",
        "satellites": [
            Satellite(
                window="TTiQL",
                module="DCS.TTiQLSatellite",
                group="dcs",
                name="TTiQL",
            ),
            Satellite(
                window="TTiQL2",
                module="DCS.TTiQLSatellite",
                group="dcs",
                name="TTiQL2",
            ),            
            Satellite(
                window="Influx",
                module="DCS.Influx",
                group="dcs",
                name="DB",
            ),
        #     # Satellite(
        #     #     window="Keithley2410",
        #     #     module="DCS.Keithley",
        #     #     group="dcs",
        #     #     name="Keithley2410",
        #     # ),        
        ],
    },
    "daq": {
        "session_name": "telescopium-daq",
        "log_subdir": "daq",
        "satellites": [
            # Satellite(
            #     window="external_trigger",
            #     module="DAQ.external_trigger_transmitter",
            #     group="daq",
            #     name="ext_trigger",
            # ),
            Satellite(
                window="receiver",
                module="DAQ.receiver",
                group="daq",
                name="receiver",
            ),
            Satellite(
                window="source_scan_chip0",
                module="DAQ.source_scan_transmitter",
                group="daq",
                name="chip0",
            ),
            Satellite(
                window="fake_data_chip1",
                module="DAQ.data_record_sender",
                group="daq",
                name="chip1",
            ),
            Satellite(
                window="fake_data_chip2",
                module="DAQ.data_record_sender",
                group="daq",
                name="chip2",
            ),
        ],
    },
}


def run(cmd: list[str], dry_run: bool = False, check: bool = True) -> subprocess.CompletedProcess | None:
    printable = " ".join(shlex.quote(part) for part in cmd)
    print(f"$ {printable}")
    if dry_run:
        return None
    return subprocess.run(cmd, check=check, text=True, capture_output=False)


def tmux_session_exists(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        text=True,
        capture_output=True,
    )
    return result.returncode == 0


def kill_tmux_session(session_name: str, dry_run: bool = False) -> None:
    run(["tmux", "kill-session", "-t", session_name], dry_run=dry_run)


def build_satellite_command(base_dir: Path, sat: Satellite, python_bin: str) -> str:
    return (
        f"cd {shlex.quote(str(base_dir))} && "
        f"{shlex.quote(python_bin)} -m {shlex.quote(sat.module)} "
        f"-g {shlex.quote(sat.group)} -n {shlex.quote(sat.name)}"
    )


def build_tmux_shell_command(base_dir: Path) -> str:
    return f"cd {shlex.quote(str(base_dir))} && exec bash"


def create_tmux_session(
    session_name: str,
    first_sat: Satellite,
    python_bin: str,
    dry_run: bool = False,
) -> None:
    shell_cmd = build_tmux_shell_command(BASE_DIR)
    sat_cmd = build_satellite_command(BASE_DIR, first_sat, python_bin)

    run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-n",
            first_sat.window,
            shell_cmd,
        ],
        check=True,
        dry_run=dry_run,
    )
    run(
            [
                "tmux",
                "set-option",
                "-t",
                f"{session_name}:{first_sat.window}",
                "remain-on-exit",
                "on",
            ],
            check=True,
            dry_run=dry_run,
        )
    run(
        [
            "tmux",
            "send-keys",
            "-t",
            f"{session_name}:{first_sat.window}",
            sat_cmd,
            "Enter",
        ],
        check=True,
        dry_run=dry_run,
    )


def add_tmux_window(session_name: str, sat: Satellite, python_bin: str, dry_run: bool = False) -> None:
    cmd = build_satellite_command(BASE_DIR, sat, python_bin)
    run(
        [
            "tmux",
            "new-window",
            "-t",
            session_name,
            "-n",
            sat.window,
            cmd,
        ],
        dry_run=dry_run,
    )


def list_panes(session_name: str) -> list[tuple[str, str]]:
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-t",
            session_name,
            "-a",
            "-F",
            "#{pane_id}\t#{window_name}",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    
def list_panes(session_name: str) -> list[tuple[str, str]]:
    result = subprocess.run(
        [
            "tmux",
            "list-panes",
            "-s",
            "-t",
            session_name,
            "-F",
            "#{pane_id}\t#{window_name}",
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    panes: list[tuple[str, str]] = []
    for line in result.stdout.strip().splitlines():
        pane_id, window_name = line.split("\t", 1)
        panes.append((pane_id, window_name))
    return panes

def safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)


def enable_logging(session_name: str, log_subdir: str, dry_run: bool = False) -> None:
    target_dir = LOG_DIR / log_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        return

    for pane_id, window_name in list_panes(session_name):
        logfile = target_dir / f"{safe_filename(window_name)}.log"
        pipe_cmd = f"cat >> {shlex.quote(str(logfile))}"
        run(
            ["tmux", "pipe-pane", "-o", "-t", pane_id, pipe_cmd],
            dry_run=False,
        )


def start_group(group_key: str, python_bin: str, kill_existing: bool, dry_run: bool) -> None:
    group_cfg = SESSIONS[group_key]
    session_name = group_cfg["session_name"]
    satellites: list[Satellite] = group_cfg["satellites"]
    log_subdir: str = group_cfg["log_subdir"]

    if not satellites:
        raise RuntimeError(f"No satellites configured for group {group_key}")

    if tmux_session_exists(session_name):
        if kill_existing:
            kill_tmux_session(session_name, dry_run=dry_run)
        else:
            raise RuntimeError(
                f"tmux session '{session_name}' already exists. Use --kill to recreate it."
            )

    create_tmux_session(session_name, satellites[0], python_bin, dry_run=dry_run)
    print(f"created ----------------------{session_name}")
    for sat in satellites[1:]:
        add_tmux_window(session_name, sat, python_bin, dry_run=dry_run)

    enable_logging(session_name, log_subdir, dry_run=dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start telescopium Constellation satellites in tmux.")
    parser.add_argument(
        "--group",
        choices=["dcs", "daq", "all"],
        default="all",
        help="Which group to start.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use inside tmux windows.",
    )
    parser.add_argument(
        "--kill",
        action="store_true",
        help="Kill existing tmux session(s) before starting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print tmux commands without executing them.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    groups = ["dcs", "daq"] if args.group == "all" else [args.group]

    try:
        for group_key in groups:
            start_group(
                group_key=group_key,
                python_bin=args.python,
                kill_existing=args.kill,
                dry_run=args.dry_run,
            )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run:
        print("\nStarted sessions:")
        for group_key in groups:
            print(f"  {SESSIONS[group_key]['session_name']}")
        print("\nAttach with:")
        for group_key in groups:
            print(f"  tmux attach -t {SESSIONS[group_key]['session_name']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
