"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Provides the entry point for the Pico TC-08 satellite
"""

from constellation.core.logging import setup_cli_logging
from constellation.core.satellite import SatelliteArgumentParser

from .PicoTC08 import PicoTC08


def main(args=None):
    """Satellite monitoring a Pico Technology USB TC-08 thermocouple data logger"""

    # Get a dict of the parsed arguments
    parser = SatelliteArgumentParser(description=main.__doc__)
    args = vars(parser.parse_args(args))

    # Set up logging
    setup_cli_logging(args.pop("level"))

    # Start satellite with remaining args
    s = PicoTC08(**args)
    s.run_satellite()


if __name__ == "__main__":
    main()
