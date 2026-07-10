from artiq.coredevice import ad9910
from artiq.coredevice import suservo
from artiq.coredevice import urukul


class ProtoRev8_alt(urukul.ProtoRev8):
    pass


class ProtoRev9_alt(urukul.ProtoRev9):
    pass


class CPLD_alt(urukul.CPLD):
    """CPLD variant whose version helper also has a distinct type.

    ARTIQ 9's compiler can merge normal ProtoRev9 objects and SU-Servo CPLD_alt
    ProtoRev9 objects, then fail because their ``cpld`` attributes point to
    different host-object types. This keeps the version helper separated too.
    Remove this with the rest of the SU-Servo workaround once ARTIQ 10 is used.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.proto_rev == urukul.STA_PROTO_REV_8:
            self.version = ProtoRev8_alt(self)
        elif self.proto_rev == urukul.STA_PROTO_REV_9:
            self.version = ProtoRev9_alt(self)


class AD9910_alt(ad9910.AD9910):
    pass


class SharedDDS_alt(suservo.SharedDDS):
    """SharedDDS variant using AD9910_alt for its hidden inner DDS.

    ARTIQ 9's compiler can confuse normal AD9910 objects, whose sync_data is
    usually SyncDataEeprom, with the hidden AD9910 inside SharedDDS, whose
    sync_data is usually SyncDataUser. Keeping the hidden DDS on a subclass
    gives the compiler a distinct host-object type.

    TODO: Remove this workaround once ARTIQ 10 is deployed; this should be fixed
    there and we should then use artiq.coredevice.suservo.SharedDDS directly.
    """

    def __init__(
        self,
        dmgr,
        cpld_device,
        pll_cp=7,
        pll_vco=5,
        sync_delay_seeds=None,
        io_update_delay=0,
        pll_en=1,
        core_device="core",
    ):
        if sync_delay_seeds is None:
            sync_delay_seeds = [-1, -1, -1, -1]

        self.core = dmgr.get(core_device)
        self.cpld = dmgr.get(cpld_device)
        self._inner_dds = AD9910_alt(
            dmgr,
            3,
            cpld_device,
            pll_cp=pll_cp,
            pll_vco=pll_vco,
            pll_en=pll_en,
        )

        self.selected_ch = 0
        self._inner_dds.io_update = suservo._MaskedIOUpdate(
            self.core,
            self._inner_dds.cpld,
            self,
            self._inner_dds.io_update,
        )

        if isinstance(sync_delay_seeds, str) or isinstance(io_update_delay, str):
            if sync_delay_seeds != io_update_delay:
                raise ValueError(
                    "When using EEPROM, sync_delay_seeds must be equal to "
                    "io_update_delay"
                )
            self.sync_data = suservo.SyncDataEeprom(
                dmgr,
                self.core,
                sync_delay_seeds,
            )
        else:
            self.sync_data = suservo.SyncDataUser(
                self.core,
                sync_delay_seeds,
                io_update_delay,
            )
