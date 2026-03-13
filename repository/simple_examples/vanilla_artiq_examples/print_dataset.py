from artiq.experiment import *

class Datasets(EnvExperiment):
    """Dataset tutorial"""
    def build(self):
        pass  # no devices used

    def run(self):
        dataset = self.__dataset_mgr
        print(dataset)