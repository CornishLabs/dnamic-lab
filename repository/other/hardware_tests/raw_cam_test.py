from artiq.experiment import *

class SimpleCameraTest(EnvExperiment):
    def build(self):
        self.setattr_device("andor_ctrl")

    def run(self):
        EXPOSURE_S = 0.4
        ROI = (0, 511, 0, 511)  # x0, x1, y0, y1 (0-based inclusive)

        c = self.andor_ctrl 

        print("Enabling cooler + setting temperature...")
        c.cooler_on()
        c.set_temperature(-50)
        
        print("Setting Shutter")
        c.set_shutter()

        # Configure camera for a single image
        print("Configuring ROI/exposure...")
        c.set_trigger_mode(0) # 0=internal
        c.set_image_region(*ROI)
        c.set_exposure_time(float(EXPOSURE_S))

        # Acquire one frame
        print("Acquiring one frame...")
        c.start_acquisition()
        c.wait()
        img = c.get_image16()

        print(f"Got image: shape={img.shape} dtype={img.dtype} bytes={img.nbytes}")
        print("Saving to dataset")
        self.set_dataset("andor.image", img, broadcast=True)
