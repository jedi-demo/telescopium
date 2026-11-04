"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC (CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Provides the entry point for the Huber satellite
"""

from constellation.core import __version__
from constellation.core.logging import setup_cli_logging
from constellation.core.satellite import SatelliteArgumentParser

from .Huber import Chiller


def main(args=None):
    """Satellite controlling a Huber Minichiller 300 OLÉ"""

    # Get a dict of the parsed arguments
    parser = SatelliteArgumentParser(description=main.__doc__, version=__version__)
    args = vars(parser.parse_args(args))

    # Set up logging
    setup_cli_logging(args.pop("level"))

    # Start satellite with remaining args
    s = Chiller(**args)
    s.run_satellite()


if __name__ == "__main__":
    main()
