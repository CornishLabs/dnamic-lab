from artiq.experiment import *     

class LEDsOn(EnvExperiment):
    
    def build(self):
        self.setattr_device("core")
        for i in range(8):
            self.setattr_device(f"led{i}")

    @kernel
    def run(self):  
        self.core.reset()                                     
        self.led0.on() # On PCB board of Kasli SOC
        self.led1.on() # Connected to L1 on front panel of Kasli SOC
        self.led2.on() # Connected to internal LED on Kasli 2.0 PCB (DU2509002)
        self.led3.on() # Connected to User L1 on Kasli 2.0 (DU2509002)
        self.led4.on() # Connected to User L2 on Kasli 2.0 (DU2509002)
        self.led5.on() # Connected to internal LED on Kasli 2.0 PCB (DU2509003)
        self.led6.on() # Connected to User L1 on Kasli 2.0 (DU2509003)
        self.led7.on() # Connected to User L2 on Kasli 2.0 (DU2509003)