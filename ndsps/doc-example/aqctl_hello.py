#!/usr/bin/env python3

# --- DRIVER CODE ----
class Hello:
    def message(self, msg):
        print("message: " + msg)
# --------------------

# --- CONTROLLER CODE ---
# To run:
# $ chmod 755 aqctl_hello.py
# $ ./aqctl_hello.py

from sipyco.pc_rpc import simple_server_loop

def main():
    simple_server_loop({"hello": Hello()}, "localhost", 3249)


"""
Defining the main function instead of putting its code 
directly in the if __name__ == "__main__" body enables 
the controller to be used as a setuptools entry point 
as well.
"""
if __name__ == "__main__":
    main()

