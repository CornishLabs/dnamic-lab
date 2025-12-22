from artiq.experiment import *


# NOTE: This code was from a forum post, it doesn't work, it's just an example pseudoish code

class ServoTester(EnvExperiment):
    def build(self):

        self.setattr_device("core")
        self.setattr_device("suservo0")  # or whatever your device db entry is
        self.setattr_device("urukul2_cpld")  # or whatever your device db entry is
        self.setattr_device("suservo0_ch0")

    @kernel
    def run(self):

        # initialize the servo + hardware (normally only done in your startup script)
        self.servo0.init()

        # the servo is currently inactive, so active it
        # after this
        #  - the servo will continuously (once per ~us) read ADC samples and store them in RAM.
        #    These are the "x" values
        #  - it will also continuously update the DDS frequency, phase and amplitude from the values stored in RAM
        #    The amplitude is the servo "y" value
        #  - if the IIRs are enabled for a DDS channel and the RF switch is open then the servo will update the
        #    y value by integrating the x values. Otherwise, the y values are held constant (e.g. use this to manually
        #   set them
        self.servo0.set_config(enable=True)

        # now, before playing with the feedback, let's check we can get some life out of the DDSs
        
        # first, set the Urukul attenuation to desired value
        self.urukul0.set_att(0, 0)

        # since the Urukuls are programmed from the servo 
        # the servo has multiple different "profiles" for each channel (DDS)
        # a profile is set of frequency, amplitude, phase data as well as an integrator
        # switching between profiles allows us to change the DDS parameters without having
        # to reset the integrators
        self.servo_ch0.set_dds(profile=0, frequency=100*MHz, offset=0)
        self.servo_ch0.set_y(0, 1)  # DDS amplitude to max manually
        self.servo_ch0.set(en_out=1, en_iir=0, profile=0)  # turn the RF switch on, but disable the integrator