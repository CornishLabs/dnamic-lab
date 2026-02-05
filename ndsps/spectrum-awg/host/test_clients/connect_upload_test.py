#!/usr/bin/env python3
import time
import numpy as np
import matplotlib.pyplot as plt

from sipyco.pc_rpc import Client

# Optional: only if pyAndorSDK2 is installed on the client machine.
# If not installed, we'll just skip setting trigger mode here.
try:
    from pyAndorSDK2 import atmcd_codes
    ANDOR_CODES = atmcd_codes
except Exception:
    ANDOR_CODES = None


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
        # Basic connectivity check
        if hasattr(c, "ping"):
            assert c.ping() is True

        print("Enabling cooler + setting temperature...")
        if hasattr(c, "cooler_on"):
            c.cooler_on()
        else:
            print("WARNING: controller has no cooler_on() method")

        if hasattr(c, "set_temperature"):
            c.set_temperature(TEMP_SETPOINT_C)
        else:
            print("WARNING: controller has no set_temperature() method")
        
        if hasattr(c, "set_shutter"):
            c.set_shutter()
        else:
            print("WARNING: controller has no set_shutter() method")

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

            if hasattr(c, "get_temperature"):
                out = c.get_temperature()
                # Common patterns:
                #  - returns temp (float/int)
                #  - returns (code, temp)
                if isinstance(out, (tuple, list)) and len(out) >= 2:
                    code, temp = out[0], out[1]
                else:
                    code, temp = None, out

                ts.append(elapsed)
                temps.append(float(temp))
                codes.append(code)

                if code is None:
                    print(f"  t={elapsed:5.2f}s  T={temp}")
                else:
                    print(f"  t={elapsed:5.2f}s  T={temp}  code={code}")
            else:
                raise RuntimeError("Controller does not provide get_temperature()")

            time.sleep(POLL_PERIOD_S)

        # Configure camera for a single image
        print("Configuring ROI/exposure...")
        if hasattr(c, "stop_acquisition"):
            try:
                c.stop_acquisition()
            except Exception:
                pass

        if hasattr(c, "set_trigger_mode"):
            # If you want INTERNAL trigger for this test shot:
            if ANDOR_CODES is not None:
                try:
                    c.set_trigger_mode(int(ANDOR_CODES.Trigger_Mode.INTERNAL))
                except Exception as e:
                    print(f"WARNING: set_trigger_mode(INTERNAL) failed: {e}")
            else:
                # If your controller expects Andor numeric constants and you don't have them here,
                # either install pyAndorSDK2 on this machine or comment this out.
                print("NOTE: pyAndorSDK2 not available in client env; skipping set_trigger_mode().")

        c.set_image_region(*ROI)
        c.set_exposure_time(float(EXPOSURE_S))

        # Acquire one frame
        print("Acquiring one frame...")
        if hasattr(c, "acquire_one"):
            img = c.acquire_one()
        else:
            # Explicit sequence
            if hasattr(c, "prepare"):
                c.prepare()
            c.start_acquisition()
            c.wait()
            if hasattr(c, "get_image16"):
                img = c.get_image16()
            else:
                raise RuntimeError("Need either acquire_one() or (wait()+get_image16()) methods")

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
