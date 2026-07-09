# Andor EMCCD camera NDSP

Images will be returned via ZMQ for latency reasons.

The host driver assumes the controller is run through SiPyCo
`simple_server_loop()` with its default `allow_parallel=False`, so camera RPC
calls are serialized by the RPC server and the driver does not add its own
threading lock.

## Useful experiment-side acquisition patterns

For an externally timed single image:

1. Call `start_external_exposure_single()` on the host/RPC side.
2. Pulse the configured camera exposure TTL from the ARTIQ kernel.
3. Call `wait_get_image16(timeout_ms=...)` on the host/RPC side.

For several externally timed images in one deterministic RTIO phase:

1. Call `start_external_exposure_series(n_images=...)` on the host/RPC side.
2. Pulse the configured camera exposure TTL `n_images` times from the ARTIQ kernel.
3. Call `wait_get_images16(n_images, timeout_ms=...)` on the host/RPC side.

`get_new_images16()` and `wait_get_images16()` read from the Andor SDK circular
buffer without aborting acquisition. This is useful for kinetic or run-till-abort
style acquisitions where the camera should remain armed while available frames are
read back.

Use `abort_acquisition(ignore_idle=True)` for cleanup paths where an idle camera is
acceptable. Other Andor SDK errors are still raised.

## EM gain

Use `set_em_gain(gain)` to select the EM output amplifier, use real EM-gain
units, force advanced high-gain access off, validate the requested gain against
the SDK range, and apply it. Use `set_em_gain(0)` or `disable_em_gain()` to turn
EM gain off again.

The driver deliberately does not expose Andor's advanced EM-gain setting.
