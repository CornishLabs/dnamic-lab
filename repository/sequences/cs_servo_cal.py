from artiq.experiment import EnvExperiment

from artiq.coredevice.ad9910 import AD9910
from artiq.coredevice.core import Core
from artiq.coredevice.suservo import Channel, SUServo
from artiq.coredevice.ttl import TTLOut
from artiq.coredevice.urukul import CPLD
from artiq.coredevice.zotino import Zotino


from ndscan.define.fragment import ExpFragment
from ndscan.define.fragment import Fragment
from ndscan.define.parameters import FloatParam
from ndscan.define.parameters import FloatParamHandle
from ndscan.define.result_channels import FloatChannel
from ndscan.runtime.api import ExecutionPolicy
from ndscan.runtime.api import ScanRequest
from ndscan.runtime.api import make_fragment_prepared_dashboard_scan_exp
from ndscan.runtime.api import prepare_child_scan

from artiq.experiment import EnvExperiment
from artiq.experiment import kernel
from artiq.experiment import rpc
from artiq.language.core import now_mu

from artiq.language.core import delay
from artiq.language.units import MHz
from artiq.language.units import V
from artiq.language.units import ms
from artiq.language.units import s
from artiq.language.units import us



import time

TWEEZER_SUSERVO_FREQ = 80.0 * MHz
TWEEZER_SUSERVO_ATTEN_DB = 8.0
TWEEZER_SUSERVO_ADC_CHANNEL = 1
TWEEZER_SUSERVO_PGIA_GAIN = 0

TWEEZER_SUSERVO_KP = -0.25
TWEEZER_SUSERVO_KI = -15_000.0
TWEEZER_SUSERVO_GAIN_LIMIT = 0.0


class SUServo1066Powermeter(ExpFragment):
    def build_fragment(self):
        self.setattr_device("core")
        self.core: Core

        self.setattr_device("suservo0")
        self.suservo0: SUServo
        self.setattr_device("suservo0_ch1")
        self.suservo0_ch1: Channel

        self.setattr_device("powermeter")
        #self.powermeter: ??

        self.setattr_param("setpoint_v",
                    FloatParam,
                    "setpoint_voltage",
                    0.0,
                    min=0.0, max=9.0)
        self.setpoint_v: FloatParamHandle

        self.setattr_result(
            "powermeter_w",
            FloatChannel,
            "Powermeter (W)",
            min=0.0,
            max=2.0,
        )

        self.setattr_result(
            "photodiode_v",
            FloatChannel,
            "photodiode (V)",
            min=0.0,
            max=10.0,
        )

        self.setattr_result(
            "asf",
            FloatChannel,
            "ASF",
            min=0.0,
            max=1.0,
        )

        # Local variables
        self._needs_hardware_init = True

    def host_setup(self):
        super().host_setup()

        self.suservo_profile = self.suservo0_ch1.servo_channel
        self.suservo_attenuator_channel = self.suservo0_ch1.servo_channel % 4
        self.suservo_cpld = self.suservo0_ch1.dds.cpld

        kernel_invariants = getattr(self, "kernel_invariants", set())
        self.kernel_invariants = kernel_invariants | {
            "suservo0",
            "suservo0_ch1",
            "suservo_profile",
            "suservo_attenuator_channel",
            "suservo_cpld",
        }

        # ndscan calls host_setup after a scheduler pause is eventually resumed.
        # This will invalidate the init state and cause a reinitialisation.
        # This is probably unnecessary as it is unlikely a seperate experiment will
        # invalidate the 'initialisation' of the device, but is safe.
        self._needs_hardware_init = True

    @rpc
    def initialise_powermeter(self):
        # Setup powermeter
        self.powermeter.set_autorange(True)
        self.powermeter.set_wavelength_nm(1066.0)

    @kernel
    def initialise_hardware_and_safe(self):
        self.core.reset()
        # Setup SUServo
        self.suservo0.init()
        self.core.break_realtime()
        delay(1.0 * ms)

        self.suservo0.set_config(enable=0)
        self.suservo0.set_pgia_mu(
            TWEEZER_SUSERVO_ADC_CHANNEL,
            TWEEZER_SUSERVO_PGIA_GAIN,
        )
        self.suservo_cpld.set_att(
            self.suservo_attenuator_channel,
            TWEEZER_SUSERVO_ATTEN_DB,
        )
        self.suservo0_ch1.set_iir(
            profile=self.suservo_profile,
            adc=TWEEZER_SUSERVO_ADC_CHANNEL,
            kp=TWEEZER_SUSERVO_KP,
            ki=TWEEZER_SUSERVO_KI,
            g=TWEEZER_SUSERVO_GAIN_LIMIT,
        )
        self.suservo0_ch1.set_dds(
            profile=self.suservo_profile,
            frequency=TWEEZER_SUSERVO_FREQ,
            offset=0.0,
        )
        self.suservo0_ch1.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch1.set(
            en_out=0, # RF switch off
            en_iir=0, # IIR integrator updates off (unservoed)
            profile=self.suservo_profile,
        )

        self.suservo0.set_config(enable=1) # Enable SUServo write cycle
        self.core.break_realtime()
        # Setup Powermeter (RPC)
        self.initialise_powermeter()

        self.set_safe()

    @kernel
    def set_safe(self):
        self.core.break_realtime()

        self.suservo0.set_config(enable=0)
        self.suservo0_ch1.set_y( # Set integrator (output) to zero
            profile=self.suservo_profile,
            y=0.0,
        )
        self.suservo0_ch1.set(
            en_out=0, # RF switch off
            en_iir=0, # IIR integrator updates off (unservoed)
            profile=self.suservo_profile,
        )
        self.suservo0.set_config(enable=1) # Enable SUServo write cycle

    @kernel
    def device_setup(self):
        if not self._needs_hardware_init:
            return
        self._needs_hardware_init = False

        self.initialise_hardware_and_safe()

    @kernel
    def device_cleanup(self):
        self.set_safe()

    @rpc
    def wait_read_and_publish_powermeter(self):
        #Wait in real time, read the host-side instrument, and publish the result.
        time.sleep(0.08)
        self.powermeter_w.push(self.powermeter.get_power())

    @kernel
    def setpoint_v_to_offset(self, setpoint_v):
        """Convert physical ADC input volts to normalized SUServo offset."""
        pgia_gain = 10.0**TWEEZER_SUSERVO_PGIA_GAIN
        return -setpoint_v * pgia_gain / 10.24

    @kernel
    def run_once(self):
        # Set SUServo
        self.core.break_realtime()
        self.suservo0.set_config(enable=0)
        self.suservo0_ch1.set_dds(
            profile=self.suservo_profile,
            frequency=TWEEZER_SUSERVO_FREQ,
            offset=self.setpoint_v_to_offset(self.setpoint_v.use()),
        )
        self.suservo0_ch1.set(
            en_out=1, # RF switch on
            en_iir=1, # IIR integrator updates on (servo)
            profile=self.suservo_profile,
        )
        self.suservo0.set_config(enable=1) # Enable SUServo write cycle
        # Get powermeter value
        # Wait until the scheduled enable event has physically happened before
        # starting the host-side settling delay.
        self.core.wait_until_mu(now_mu())
        self.wait_read_and_publish_powermeter()
        # Stop SUServo IIR
        self.core.break_realtime()
        self.suservo0.set_config(enable=0)
        delay(5*us)
        self.photodiode_v.push(self.suservo0.get_adc(1))
        delay(10*us)
        self.asf.push(self.suservo0_ch1.get_y(1))
        self.core.break_realtime()
        self.suservo0.set_config(enable=1) # Re-enable the SUServo

SUServo1066PowermeterExp = make_fragment_prepared_dashboard_scan_exp(
    SUServo1066Powermeter,
    max_rtio_underflow_retries=0,
)