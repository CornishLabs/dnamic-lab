# Write a sequence that changes the temperature, gives it time to settle,
# then returns the average dark counts. This requires the shutter to be closed.

from ndscan.experiment import *
import numpy as np
import time

from collections import deque

class DarkCounts(ExpFragment):
    def build_fragment(self):
        self.setattr_param("target_temperature",
                           IntParam,
                           "Target sensor temperature",
                           -20,
                           min=-70,max=20)
        
        self.setattr_result("dark_counts_average")
        self.setattr_result("dark_counts_stddev")

        self.setattr_device("core")
        self.setattr_device("andor_ctrl")

    def run_once(self):


        EXPOSURE_S = 0.3
        ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (0-based inclusive)

        target_temp = self.target_temperature.get()


        q = deque(maxlen=8)

        self.andor_ctrl.cooler_on()
        self.andor_ctrl.set_temperature(target_temp)
        
        # Wait a bit before looking at temperature
        run=True
        i=0
        while run:
            time.sleep(5)
            i+=1
            ret, temp = self.andor_ctrl.get_temperature()
            q.append(temp)
            avg = sum(q) / len(q)
            dev = max(q)-min(q)
            print(f"temp={temp:.2f}, avg={avg:.2f}")
            run = (((abs(avg-target_temp)>1.5) or dev>2.5) and (i<100)) or (i<8)
        
        # Temperature has reached setpoint
        # Take a picture
        print("Taking a picture")
        print("Setting Shutter")
        self.andor_ctrl.set_shutter(mode=2) # 2=Permenantly closed

        # Configure camera for a single image
        print("Configuring ROI/exposure...")
        self.andor_ctrl.set_trigger_mode(0) # 0=internal, 6 = External start, 7 = External exposure
        self.andor_ctrl.set_image_region(*ROI)
        self.andor_ctrl.set_exposure_time(float(EXPOSURE_S))

        # Acquire one frame
        print("Acquiring one frame...")
        self.andor_ctrl.start_acquisition()
        self.andor_ctrl.wait()
        img = self.andor_ctrl.get_image16()

        print(f"Got image: shape={img.shape} dtype={img.dtype} bytes={img.nbytes}")
        print("Saving to dataset")
        self.set_dataset("andor.image", img, broadcast=True)    

        print("Aborting Acquisition")
        self.andor_ctrl.abort_acquisition()

        self.dark_counts_average.push(np.median(img))
        self.dark_counts_stddev.push(np.std(img))

DarkCountsExp = make_fragment_scan_exp(DarkCounts)