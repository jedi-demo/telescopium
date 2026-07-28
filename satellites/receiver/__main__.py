from constellation.core.logging import setup_cli_logging
from constellation.core.satellite import SatelliteArgumentParser
from tj_receiver import TJMonopix2H5Receiver


def main(args=None):
    """Controlling of a Satellite for TJ-Monopix2"""

    parser = SatelliteArgumentParser(description=main.__doc__)
    args = vars(parser.parse_args(args))

    setup_cli_logging(args.pop("level"))

    r = TJMonopix2H5Receiver(**args)
    r.run_satellite()


if __name__ == "__main__":
    main()
