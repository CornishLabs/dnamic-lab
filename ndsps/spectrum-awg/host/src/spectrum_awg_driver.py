import threading
from typing import Optional, Tuple

import numpy as np


class SpectrumAWGCompilerUploader:

    def __init__(self, simulation: bool = False):
        self.simulation = bool(simulation)
        
    def ping(self) -> bool:
        return True
    
    def plan_phase_compile_upload(self):
        # Attach calibration
        # Phase plan
        # Compile to samples
        # Upload to card
        pass



