"""
SPDX-FileCopyrightText: 2026 PixLab, IFIC(CSIC-UV)
SPDX-License-Identifier: EUPL-1.2

Provides the entry point for the AIM-TTi QL355TP power supply satellite.
"""
from constellation.core.logging import setup_cli_logging
from constellation.core.satellite import SatelliteArgumentParser
from .TTiQLSatellite import TTiQLSatellite

def main(args=None):
    """Satellite controlling a TTi power supply"""
    # Get a dict of the parsed arguments
    parser = SatelliteArgumentParser(description="TTi QL Series II Satellite")
    args = vars(parser.parse_args(args))
    
    # Set up logging
    setup_cli_logging(args.pop("level"))

    # Start satellite with remaining args
    s = TTiQLSatellite(**args)
    s.run_satellite()

if __name__ == "__main__":
    main()
