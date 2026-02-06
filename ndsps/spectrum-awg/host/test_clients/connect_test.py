#!/usr/bin/env python3
import numpy as np

from sipyco.pc_rpc import Client


def main():
    # Change these to match your controller config
    HOST = "127.0.0.1"
    PORT = 4015
    TARGET = "awg" 

    c = Client(HOST, PORT, TARGET)
    try:
        # Basic connectivity check
        
        assert c.ping() is True

        print("Atempting to get some card info")
        c.print_card_info()

    finally:
        c.close_rpc()


if __name__ == "__main__":
    main()
