from artiq.experiment import EnvExperiment


class ReadPowermeter(EnvExperiment):
    """Configure the PM100D and print one power reading."""

    def build(self):
        # Controller devices are acquired in exactly the same way as local
        # ARTIQ devices. Here, ``powermeter`` refers to the controller entry in
        # device_db.py and becomes a SiPyCo RPC proxy on the experiment host.
        self.setattr_device("powermeter")

    def run(self):
        # PM100D communication is ordinary host-side USB I/O, so these calls
        # deliberately remain outside an @kernel method.
        #
        # Each experiment establishes every measurement setting it relies on;
        # it does not assume that a previous user left the meter configured in
        # a particular state.
        self.powermeter.set_autorange(True)
        self.powermeter.set_wavelength_nm(856.0)

        power_w = self.powermeter.get_power()
        print(f"PM100D power at 852 nm: {power_w:.12g} W")
