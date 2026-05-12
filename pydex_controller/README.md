# PyDex Controller Template

This folder contains a small template for receiving command strings from PyDex
and applying them to some hardware. The hardware-specific part is intentionally
left empty: copy `pydex_command_listener_template.py` for a specific device and
implement `setup()` and `do_something_with_parsed_data()`.

## Old Architecture

The original files used the names `PyServer` and `PyClient`. Those names were
technically correct at the TCP socket layer, but misleading at the application
layer.

The old `PyServer` was the process that bound/listened on a TCP port. In the
real system, that is the PyDex side. However, this "server" was not mainly
receiving commands. It owned an outgoing message queue, `__mq`, containing
commands that should be sent to the device controller. When the device process
connected, `PyServer` popped one queued command and sent it.

The old `PyClient` was the process that connected to the TCP server. In this
project, that was historically `tcpControl.py`, running next to the hardware.
So the device controller was technically a TCP client, but conceptually it was
the command receiver/listener. It repeatedly connected to PyDex, received one
framed command, echoed a framed response back, emitted the command through a Qt
signal, and then disconnected.

In other words:

```text
PyDex / simulator:
    application role: command sender
    TCP role: server
    queue/bucket: outgoing commands waiting to be fetched

hardware command-listener process:
    application role: command receiver and hardware actor
    TCP role: client
    queue/bucket: usually no real queue; it handles commands as received
```

That inversion is the main source of confusion. "Server" meant "owns the
listening socket", not "receives the instruction". "Client" meant "opens the
TCP connection", not "initiates the experiment command".

## Wire Protocol

The wire protocol is unusual but simple:

1. The command-listener process connects to the PyDex TCP server.
2. PyDex sends 4 bytes: unsigned integer enum.
3. PyDex sends 4 bytes: unsigned integer payload length.
4. PyDex sends payload bytes: usually text like `set_data=...`.
5. The command listener sends back the same framed shape as an acknowledgement.
6. Both sides close that connection.
7. The command listener repeats this polling loop.

The only compatibility this project keeps is the transport/wire format and the
command text schema sent by PyDex, for example:

```text
set_data=[[3, "time (us)", 10.0]]
save_all=Z:\\Tweezer\\Experimental Results\\Run\\file.txt
```

## Current Architecture

This template keeps the same wire protocol so the real PyDex sender does not
need to change. The main simplification is that the Qt event loop, QThread
subclasses, and Qt signals are gone from this control path.

The code is split by responsibility:

```text
pydex_transport.py:
    Low-level PyDex frame read/write helpers, plus the polling loop used by the
    hardware-side controller. This knows about the enum + length + text socket
    framing only.

simulate_pydex.py:
    A small local stand-in for the real PyDex side. It binds/listens like PyDex
    and serves queued command frames to the command-listener template.

pydex_command_listener_template.py:
    A generic hardware-controller template. It parses command strings and calls
    one function where device-specific action should happen.
```

The conceptual flow should be read as:

```text
PyDex sends a command -> the listener parses it -> user code changes hardware
```

Even though the TCP connection is still opened in the opposite direction:

```text
the listener connects to PyDex -> PyDex hands over one queued command
```

## Local Test

From the repo root, start the fake PyDex sender in one terminal:

```bash
python -m pydex_controller.simulate_pydex --host 127.0.0.1 --port 8636 --log-level DEBUG
```

Then start the generic command listener in another terminal:

```bash
python -m pydex_controller.pydex_command_listener_template --host 127.0.0.1 --port 8636 --log-level DEBUG
```

`simulate_pydex.py` exits after it has served its queued messages.
`pydex_command_listener_template.py` keeps polling until stopped with `Ctrl-C`.
