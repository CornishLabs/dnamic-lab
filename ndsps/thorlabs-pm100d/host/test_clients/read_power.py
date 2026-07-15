#!/usr/bin/env python3

from sipyco.pc_rpc import Client


def main() -> None:
    client = Client("127.0.0.1", 4020, "powermeter")
    try:
        assert client.ping() is True
        client.set_autorange(True)
        client.set_wavelength_nm(780.0)
        print(f"Power: {client.get_power():.6g} W")
    finally:
        client.close_rpc()


if __name__ == "__main__":
    main()
