import time

from artiq.experiment import *
from artiq.master.scheduler import Scheduler


class BlockPipeline(EnvExperiment):
    """
    Block a pipeline until cancelled
    """

    def build(self):
        self.setattr_device("scheduler")
        self.scheduler: Scheduler

    def run(self):
        time.sleep(0.1)

        while True:
            if self.scheduler.check_pause():
                print("Quitting")
                return
