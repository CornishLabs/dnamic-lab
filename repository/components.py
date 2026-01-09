from ndscan.experiment import (
    ExpFragment,
    FloatParam, IntParam,
    IntChannel,
    MHz, us, ms, OpaqueChannel
)
import random, numpy as np
import time
from repository.models.atom_response import image_from_probs_and_locs

class PrepareAtom(ExpFragment):
    def build_fragment(self):
        self.setattr_param("cool_time", FloatParam, "Cooling time", default=2.0*ms, min=0.0, unit="ms")
    
    def run_once(self):
        ct = self.cool_time.get()
        time.sleep(ct)
        print(f"[Prepare] {self.cool_time.get()/ms:.1f} ms → prepared")

class Pulse(ExpFragment):
    def build_fragment(self):
        self.setattr_param("frequency",   FloatParam, "Drive freq",    default=10.0*MHz, unit="MHz")
        self.setattr_param("duration", FloatParam, "Duration", default=0.48*us,  unit="us", min=0.0)

    def run_once(self):
        print(f"[Pulse] f={self.frequency.get()/MHz:.3f} MHz, t={self.duration.get()/us:.2f} µs")

class ReadoutFluorescence(ExpFragment):
    def build_fragment(self):
        self.setattr_param("p_bright",    FloatParam, "Bright prob", default=0.5, min=0.0, max=1.0)
        # self.setattr_param("threshold",   IntParam,   "Threshold",   default=600,    min=0)

        self.setattr_result("counts",          OpaqueChannel)
        # self.setattr_result("is_bright_class", OpaqueChannel)

    def run_once(self):
        pb  = self.p_bright.get()
        # thr = self.threshold.get()

        image = image_from_probs_and_locs([(6+6*i,16,pb) for i in range(8)])
        self.set_dataset("last_image",image,broadcast=True)

        try:
            rois = self.get_dataset("rois",archive=False)
        except KeyError:
            # sensible default
            rois = [[(15, 18, 5, 8)], [(15, 18, 11, 14)], [(15, 18, 17, 20)], [(15, 18, 23, 26)], [(15, 18, 29, 32)], [(15, 18, 35, 38)], [(15, 18, 41, 44)], [(15, 18, 47, 50)]]
            self.set_dataset("rois", rois, broadcast=True)

        rois_shape = np.shape(rois)
        
        # Sum counts in each ROI and classify
        counts = np.empty((rois_shape[0],rois_shape[1]), dtype=np.int16)
        for g_i, roi_g in enumerate(rois):
            for roi_i, (y0,y1,x0,x1) in enumerate(roi_g):
                cts = int(image[y0:y1, x0:x1].sum())
                counts[g_i,roi_i] = cts
        
        self.counts.push(counts)

        print(f"[Readout] p_b={pb:.3f}")
