#!/usr/bin/env python3
import time
import numpy as np
import matplotlib.pyplot as plt

from sipyco.pc_rpc import Client


def main():
    # Change these to match your controller config
    HOST = "127.0.0.1"
    PORT = 4010
    TARGET = "camera"  # e.g. "camera" or a serial string, depending on your controller

    TEMP_SETPOINT_C = -50
    POLL_SECONDS = 20.0
    POLL_PERIOD_S = 0.5

    EXPOSURE_S = 0.05
    ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (0-based inclusive)

    c = Client(HOST, PORT, TARGET)
    try:
        assert c.ping() is True

        print("Enabling cooler + setting temperature...")
        c.cooler_on()
        c.set_temperature(TEMP_SETPOINT_C)
        c.set_shutter_permanently_open()

        # Temperature polling
        print(f"Polling temperature for {POLL_SECONDS:.1f} s ...")
        t0 = time.perf_counter()
        ts = []
        temps = []
        codes = []

        while True:
            elapsed = time.perf_counter() - t0
            if elapsed >= POLL_SECONDS:
                break

            code, temp = c.get_temperature()
            ts.append(elapsed)
            temps.append(float(temp))
            codes.append(code)
            print(f"  t={elapsed:5.2f}s  T={temp}  code={code}")

            time.sleep(POLL_PERIOD_S)

        # Configure camera for a single image
        print("Configuring ROI/exposure...")
        c.abort_acquisition(ignore_idle=True)
        c.set_trigger_mode(0)  # Andor Trigger_Mode.INTERNAL
        c.set_image_region(*ROI)
        c.set_exposure_time(float(EXPOSURE_S))

        # Acquire one frame
        print("Acquiring one frame...")
        c.prepare()
        c.start_acquisition()
        img = c.wait_get_image16(timeout_ms=5000)

        img = np.asarray(img, dtype=np.uint16)
        print(f"Got image: shape={img.shape} dtype={img.dtype} bytes={img.nbytes}")

        # Plot results
        fig1 = plt.figure()
        plt.plot(ts, temps)
        plt.xlabel("Time (s)")
        plt.ylabel("Temperature (°C)")
        plt.title("Camera temperature vs time")
        plt.grid(True)

        fig2 = plt.figure()
        plt.imshow(img, origin="lower")
        plt.colorbar(label="Counts")
        plt.title(f"Andor image {img.shape[1]}×{img.shape[0]}, exposure={EXPOSURE_S}s")
        plt.xlabel("X (px)")
        plt.ylabel("Y (px)")

        plt.show()

    finally:
        c.close_rpc()


if __name__ == "__main__":
    main()
