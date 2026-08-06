from constellation.core.logging import setup_cli_logging
from constellation.core.transmitter_satellite import TransmitterSatelliteArgumentParser
from .tj_transmitter import FakePacketSender


def main(args=None):
    """Controlling of a Satellite for TJ-Monopix2"""

    parser = TransmitterSatelliteArgumentParser(description=main.__doc__)
    args = vars(parser.parse_args(args))

    setup_cli_logging(args.pop("level"))

    s = FakePacketSender(**args)
    s.run_satellite()


if __name__ == "__main__":
    main()
