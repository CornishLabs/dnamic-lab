import time
import numpy as np
from sipyco.pc_rpc import Client

def main():
    c = Client("127.0.0.1", 4010, "camera")
    try:
        # configure ROI and exposure similar to your experiments
        c.set_image_region(0, 511, 0, 511)
        c.set_exposure_time(0.05)

        # warmup
        for _ in range(3):
            _ = c.get_temperature()

        n = 20
        lat = []
        for _ in range(n):
            t0 = time.perf_counter()
            img = c.acquire_one()
            t1 = time.perf_counter()
            lat.append((t1 - t0) * 1e3)
            assert isinstance(img, np.ndarray)

        print(f"acquire_one(): mean={sum(lat)/len(lat):.2f} ms min={min(lat):.2f} ms max={max(lat):.2f} ms")
        print("image:", img.shape, img.dtype, img.nbytes, "bytes")
    finally:
        c.close_rpc()

if __name__ == "__main__":
    main()
