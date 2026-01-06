#!/usr/bin/env python3

# -- client code ----

from sipyco.pc_rpc import Client


def main():
    remote = Client("localhost", 3249, "hello")
    try:
        remote.message("Hello World!")
    finally:
        remote.close_rpc()

if __name__ == "__main__":
    main()
