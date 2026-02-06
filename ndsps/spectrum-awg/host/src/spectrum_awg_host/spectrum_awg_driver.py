import threading
from typing import Optional, Tuple

import numpy as np
import spcm

class SpectrumAWGCompilerUploader:

    def __init__(self, serial_number: int, simulation: bool = False):
        self.serial_number = int(serial_number)
        self.simulation = bool(simulation)
        
    def ping(self) -> bool:
        return True
    
    def plan_phase_compile_upload(self):
        # Attach calibration
        # Phase plan
        # Compile to samples
        # Upload to card
        pass

    def print_card_info(self):
        with spcm.Card(serial_number=self.serial_number) as card:
            product_name = card.product_name()
            status = card.status()
            print(f"Product: {product_name}, card status: {status}")



