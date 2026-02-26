from artiq.language.units import MHz, V

from ndscan.experiment import ExpFragment, make_fragment_scan_exp
from ndscan.experiment.parameters import FloatParam

class ParametersWithUnits(ExpFragment):

    # The point of this example is to show that the value of this param handle is in base units, 
    # In this case Hz. I.e. param.get() will return 8.8e6 rather than 8.8, therefore the name 
    # of the parameter shouldn't have units in it.
    def build_fragment(self):
        self.setattr_param("param",
                           FloatParam,
                           "Quad coil servo setpoint voltage",
                           8.8*MHz,
                           min=0*MHz, max=10*MHz, unit='MHz')

    def run_once(self):
        print(self.param.get())

ParametersWithUnitsExperiment = make_fragment_scan_exp(ParametersWithUnits)
