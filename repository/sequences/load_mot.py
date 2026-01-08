# Write a sequence that changes the temperature, gives it time to settle,
# then returns the average dark counts. This requires the shutter to be closed.

from ndscan.experiment import *
import numpy as np
import time

class LoadRbMOT(ExpFragment):
    def build_fragment(self):
        # Knobs
        self.setattr_param("cool_frequency",
                           FloatParam,
                           "Cool light AOM frequency",
                           110*MHz,
                           min=-40*MHz,max=40*MHz)
        
        self.setattr_param("repump_frequency",
                           FloatParam,
                           "Repump light AOM frequency",
                           110*MHz,
                           min=-40*MHz,max=40*MHz)
        
        self.setattr_param("cool_dds_amp",
                            FloatParam,
                            "Cool light AOM dds amp (0-1)",
                            0.6,
                            min=0,max=1)
        
        self.setattr_param("repump_dds_amp",
                            FloatParam,
                            "Repump light AOM dds amp (0-1)",
                            0.6,
                            min=0,max=1)
        
        self.setattr_param("cool_dds_att",
                            FloatParam,
                            "Cool light AOM dds attenuator 0.5db res",
                            3.0*db,
                            min=0*db,max=30*db)
        
        self.setattr_param("repump_dds_att",
                            FloatParam,
                            "Repump light AOM dds amp 0.5db res",
                            3.0*db,
                            min=0*db,max=30*db)
        
        self.setattr_param("quad_setpoint",
                           FloatParam,
                           "Quad coil servo setpoint voltage",
                           )
        
        self.setattr_param("NS_setpoint",
                    FloatParam,
                    "N/S Shims servo setpoint voltage",
                    )
        
        self.setattr_param("EW_setpoint",
                    FloatParam,
                    "E/W Shims servo setpoint voltage",
                    )
        
        self.setattr_param("UD_setpoint",
                    FloatParam,
                    "U/D Shims servo setpoint voltage",
                    )
        
        self.setattr_param("preload_time",
                           FloatParam,
                           "Time to load MOT before imaging starts",
                           3*s,
                           min=1*ms, max=30*s
                           )
        
        self.setattr_param("exposure_time",
                           FloatParam,
                           "Time spent fluorescing while exposing",
                           1*s,
                           min=1*ms, max=30*s
                           )

        # Results
        self.setattr_result("mot_image", OpaqueChannel)

        # Device drivers
        self.setattr_device("core")
        self.setattr_device("andor_ctrl")

    def run_once(self):
        pass
        # Turn on MOT beams

        # Expose camera

        # Put image into dataset as opaque.



MOTLoadExp = make_fragment_scan_exp(LoadRbMOT)