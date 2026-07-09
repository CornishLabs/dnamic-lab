import time

import numpy as np
from pyAndorSDK2 import atmcd
from pyAndorSDK2 import atmcd_codes
from pyAndorSDK2 import atmcd_errors


class AndorError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, code_name: str | None = None):
        """Store an Andor SDK failure message with the numeric return code."""
        super().__init__(message)
        self.code = code
        self.code_name = code_name


class AndorTimeoutError(AndorError):
    pass


class AndorEMCCD:
    """
    Small Andor SDK2 EMCCD wrapper suitable as a SiPyCo RPC target.

    SiPyCo's simple_server_loop() serializes RPC calls by default
    (allow_parallel=False), so this driver intentionally does not add its own
    lock. Keep allow_parallel disabled for this controller.
    """

    _EM_OUTPUT_AMPLIFIER = 0
    _EM_GAIN_MODE_REAL = 3
    _SIM_EM_GAIN_RANGE = (0, 300)

    def __init__(self, config_path: str = "/usr/local/etc/andor", simulation: bool = False):
        """Create the driver object; call connect() before using the camera."""
        self.simulation = bool(simulation)
        self.config_path = config_path
        self.codes = atmcd_codes
        self.errors = atmcd_errors.Error_Codes

        self._sdk = None
        self._initialized = False
        self._detector = (0, 0)
        self._roi = (0, 511, 0, 511)
        self._em_gain = 0
        self._simulation_frame_index = 0

    # --------- utilities ---------

    def ping(self) -> bool:
        """Return True for controller liveness checks."""
        return True

    def _code_name(self, code: int) -> str:
        """Return the SDK enum name for a numeric Andor return/status code."""
        try:
            return self.errors(int(code)).name
        except ValueError:
            return f"UNKNOWN_{int(code)}"

    def _check(self, ret: int, where: str, ignore: tuple[int, ...] = ()) -> None:
        """Raise AndorError unless an SDK return code is successful or ignored."""
        ret = int(ret)
        if ret == int(self.errors.DRV_SUCCESS) or ret in ignore:
            return
        code_name = self._code_name(ret)
        raise AndorError(f"{where} failed: {code_name} ({ret})", ret, code_name)

    def _require_init(self) -> None:
        """Require connect() to have completed before accessing camera state."""
        if not self._initialized:
            raise AndorError("Camera not initialized")

    def _sdk_required(self):
        """Return the SDK object after checking that real-camera access is valid."""
        self._require_init()
        if self._sdk is None:
            raise AndorError("Camera SDK object is not available")
        return self._sdk

    def _raise_timeout(self, where: str, timeout_ms: int | None) -> None:
        """Raise a consistent timeout error for SDK waits that see no new data."""
        code = int(self.errors.DRV_NO_NEW_DATA)
        code_name = self._code_name(code)
        if timeout_ms is None:
            raise AndorTimeoutError(f"{where} timed out: {code_name} ({code})", code, code_name)
        raise AndorTimeoutError(
            f"{where} timed out after {timeout_ms} ms: {code_name} ({code})",
            code,
            code_name,
        )

    def _image_shape(self) -> tuple[int, int]:
        """Return the current ROI image shape as (height, width)."""
        x0, x1, y0, y1 = self._roi
        return y1 - y0 + 1, x1 - x0 + 1

    def _image_size(self) -> int:
        """Return the number of pixels in the current ROI."""
        height, width = self._image_shape()
        return height * width

    def _buffer_to_uint16(self, buf, count: int) -> np.ndarray:
        """Copy an SDK buffer into a NumPy uint16 array."""
        try:
            return np.frombuffer(buf, dtype=np.uint16, count=count).copy()
        except TypeError:
            return np.array(buf, dtype=np.uint16, copy=True)

    def _simulation_images(self, n_images: int) -> np.ndarray:
        """Generate deterministic synthetic images for offline tests."""
        height, width = self._image_shape()
        base = np.arange(width * height, dtype=np.uint16).reshape(height, width)
        images = np.empty((n_images, height, width), dtype=np.uint16)
        for i in range(n_images):
            images[i] = base + np.uint16(self._simulation_frame_index + i)
        self._simulation_frame_index += n_images
        return images

    def _new_image_range(self) -> tuple[int, int, int]:
        """Return count, first, last for unread images in the SDK circular buffer."""
        if self.simulation:
            return 1, 1, 1

        ret, first, last = self._sdk_required().GetNumberNewImages()
        if int(ret) == int(self.errors.DRV_NO_NEW_DATA):
            return 0, 0, -1
        self._check(ret, "GetNumberNewImages")

        first = int(first)
        last = int(last)
        return last - first + 1, first, last

    def _read_images16(self, first: int, last: int) -> np.ndarray:
        """Read and drain a contiguous 16-bit image range from the SDK buffer."""
        n_images = int(last) - int(first) + 1
        if n_images <= 0:
            raise ValueError("Image range must contain at least one image")
        if self.simulation:
            return self._simulation_images(n_images)

        image_size = self._image_size()
        total_size = n_images * image_size
        ret, buf, validfirst, validlast = self._sdk_required().GetImages16(
            first,
            last,
            total_size,
        )
        self._check(ret, "GetImages16")

        if int(validfirst) != int(first) or int(validlast) != int(last):
            raise AndorError(
                "GetImages16 returned an unexpected valid image range "
                f"{validfirst}-{validlast}; requested {first}-{last}"
            )

        height, width = self._image_shape()
        return self._buffer_to_uint16(buf, total_size).reshape(n_images, height, width)

    def _wait_for_new_images(self, n_images: int, timeout_ms: int | None) -> tuple[int, int]:
        """Wait until at least n_images are unread, then return the read range."""
        if n_images <= 0:
            raise ValueError("n_images must be positive")
        if self.simulation:
            return 1, n_images

        if timeout_ms is None:
            deadline = None
        else:
            timeout_ms = int(timeout_ms)
            deadline = time.monotonic() + timeout_ms / 1000.0

        while True:
            count, first, _last = self._new_image_range()
            if count >= n_images:
                return first, first + n_images - 1

            if deadline is None:
                ret = self._sdk_required().WaitForAcquisition()
            else:
                remaining_ms = int(max(0.0, (deadline - time.monotonic()) * 1000.0))
                if remaining_ms <= 0:
                    self._raise_timeout(f"Waiting for {n_images} image(s)", timeout_ms)
                ret = self._sdk_required().WaitForAcquisitionTimeOut(remaining_ms)

            if int(ret) == int(self.errors.DRV_NO_NEW_DATA):
                self._raise_timeout(f"Waiting for {n_images} image(s)", timeout_ms)
            self._check(ret, "WaitForAcquisition")

    def _select_real_em_gain_mode(self) -> None:
        """Use real EM-gain units and keep advanced high-gain access disabled."""
        sdk = self._sdk_required()
        self._check(sdk.SetEMGainMode(self._EM_GAIN_MODE_REAL), "SetEMGainMode(REAL)")
        self._check(
            sdk.SetEMAdvanced(0),
            "SetEMAdvanced(OFF)",
            ignore=(int(self.errors.DRV_NOT_AVAILABLE),),
        )

    # --------- lifecycle / telemetry ---------

    def connect(self) -> None:
        """Initialise the SDK, query detector size, and set a basic image mode."""
        if self._initialized:
            return

        if self.simulation:
            self._detector = (512, 512)
            self._initialized = True
            return

        self._sdk = atmcd("")
        self._check(self._sdk.Initialize(self.config_path), "Initialize")

        ret, xpixels, ypixels = self._sdk.GetDetector()
        self._check(ret, "GetDetector")
        self._detector = (int(xpixels), int(ypixels))

        self._check(
            self._sdk.SetReadMode(int(self.codes.Read_Mode.IMAGE)),
            "SetReadMode(IMAGE)",
        )
        self._check(
            self._sdk.SetAcquisitionMode(int(self.codes.Acquisition_Mode.SINGLE_SCAN)),
            "SetAcquisitionMode(SINGLE_SCAN)",
        )
        self._initialized = True

    def close(self) -> None:
        """Shut down the SDK connection and mark the camera disconnected."""
        if not self._initialized:
            return
        try:
            if not self.simulation:
                self._check(self._sdk_required().ShutDown(), "ShutDown")
        finally:
            self._sdk = None
            self._initialized = False

    def get_serial(self) -> int:
        """Return the camera serial number, or 0 in simulation."""
        if self.simulation:
            self._require_init()
            return 0
        ret, serial = self._sdk_required().GetCameraSerialNumber()
        self._check(ret, "GetCameraSerialNumber")
        return int(serial)

    def get_detector(self) -> tuple[int, int]:
        """Return the full detector size as (x_pixels, y_pixels)."""
        self._require_init()
        return self._detector

    def get_status(self) -> int:
        """Return the current SDK camera status code."""
        if self.simulation:
            self._require_init()
            return int(self.errors.DRV_IDLE)
        ret, status = self._sdk_required().GetStatus()
        self._check(ret, "GetStatus")
        return int(status)

    def get_status_name(self) -> str:
        """Return the current SDK camera status as a readable enum name."""
        return self._code_name(self.get_status())

    def cooler_on(self) -> None:
        """Enable the camera cooler."""
        if self.simulation:
            self._require_init()
            return
        self._check(self._sdk_required().CoolerON(), "CoolerON")

    def set_cooler_mode(self, maintain_on_shutdown: bool) -> None:
        """Choose whether the cooler stays cold after SDK shutdown."""
        if self.simulation:
            self._require_init()
            return
        self._check(
            self._sdk_required().SetCoolerMode(1 if maintain_on_shutdown else 0),
            "SetCoolerMode",
        )

    def set_temperature(self, temp_c: int) -> None:
        """Set the cooler target temperature in degrees Celsius."""
        if self.simulation:
            self._require_init()
            return
        self._check(self._sdk_required().SetTemperature(int(temp_c)), "SetTemperature")

    def get_temperature(self) -> tuple[int, int]:
        """Return (SDK temperature status code, current temperature Celsius)."""
        self._require_init()
        if self.simulation:
            return int(self.errors.DRV_SUCCESS), -75
        ret, temp = self._sdk_required().GetTemperature()
        return int(ret), int(temp)

    def get_preamp_gains(self) -> list[dict[str, int | float | str]]:
        """Return the SDK preamp-gain table as index/gain/text entries."""
        self._require_init()
        if self.simulation:
            return [
                {"index": 0, "gain": 1.0, "text": "sim 1x"},
                {"index": 1, "gain": 2.0, "text": "sim 2x"},
                {"index": 2, "gain": 4.0, "text": "sim 4x"},
            ]

        sdk = self._sdk_required()
        ret, n_gains = sdk.GetNumberPreAmpGains()
        self._check(ret, "GetNumberPreAmpGains")

        gains = []
        for index in range(int(n_gains)):
            ret, gain = sdk.GetPreAmpGain(index)
            self._check(ret, "GetPreAmpGain")

            text = ""
            ret, name = sdk.GetPreAmpGainText(index, 32)
            if int(ret) == int(self.errors.DRV_SUCCESS):
                text = name.value.decode(errors="replace")

            gains.append({"index": index, "gain": float(gain), "text": text})
        return gains

    def get_em_gain(self) -> int:
        """Return the current EMCCD gain reported by the SDK."""
        self._require_init()
        if self.simulation:
            return self._em_gain

        ret, gain = self._sdk_required().GetEMCCDGain()
        self._check(ret, "GetEMCCDGain")
        self._em_gain = int(gain)
        return self._em_gain

    def get_em_gain_range(self) -> tuple[int, int]:
        """Return the valid real EM-gain range with advanced gain disabled."""
        self._require_init()
        if self.simulation:
            return self._SIM_EM_GAIN_RANGE

        self._select_real_em_gain_mode()
        ret, low, high = self._sdk_required().GetEMGainRange()
        self._check(ret, "GetEMGainRange")
        return int(low), int(high)

    def set_em_gain(self, gain: int) -> None:
        """Set real EM gain, always keeping advanced high-gain access disabled."""
        self._require_init()
        gain = int(gain)
        if gain < 0:
            raise ValueError("EM gain must be non-negative")

        if self.simulation:
            low, high = self._SIM_EM_GAIN_RANGE
            if gain != 0 and not low <= gain <= high:
                raise ValueError(f"EM gain {gain} outside valid range {low}-{high}")
            self._em_gain = gain
            return

        self._select_real_em_gain_mode()
        ret, low, high = self._sdk_required().GetEMGainRange()
        self._check(ret, "GetEMGainRange")
        low = int(low)
        high = int(high)
        if gain != 0 and not low <= gain <= high:
            raise ValueError(f"EM gain {gain} outside valid range {low}-{high}")

        self._check(
            self._sdk_required().SetOutputAmplifier(self._EM_OUTPUT_AMPLIFIER),
            "SetOutputAmplifier(EM)",
        )
        self._check(self._sdk_required().SetEMCCDGain(gain), "SetEMCCDGain")
        self._em_gain = gain

    def disable_em_gain(self) -> None:
        """Turn EM gain off by setting the EMCCD gain to zero."""
        self.set_em_gain(0)

    # --------- low-level configuration ---------

    def set_readout_profile(
        self,
        *,
        output_amplifier: int = _EM_OUTPUT_AMPLIFIER,
        hsspeed_index: int,
        vsspeed_index: int,
        preamp_gain_index: int,
        ad_channel: int | None = None,
    ) -> None:
        """Set amplifier, optional AD channel, horizontal/vertical speeds, and preamp gain."""
        if self.simulation:
            self._require_init()
            return

        sdk = self._sdk_required()
        output_amplifier = int(output_amplifier)

        self._check(sdk.SetOutputAmplifier(output_amplifier), "SetOutputAmplifier")
        if ad_channel is not None:
            self._check(sdk.SetADChannel(int(ad_channel)), "SetADChannel")
        self._check(
            sdk.SetHSSpeed(output_amplifier, int(hsspeed_index)),
            "SetHSSpeed",
        )
        self._check(sdk.SetVSSpeed(int(vsspeed_index)), "SetVSSpeed")

        preamp_gain_index = int(preamp_gain_index)
        preamp_gains = self.get_preamp_gains()
        valid_preamp_indices = [entry["index"] for entry in preamp_gains]
        if preamp_gain_index not in valid_preamp_indices:
            raise AndorError(
                f"Preamp gain index {preamp_gain_index} is invalid. "
                f"Valid SDK indices are {valid_preamp_indices}; "
                "old UI settings may be 1-based."
            )

        if ad_channel is not None:
            ret, available = sdk.IsPreAmpGainAvailable(
                int(ad_channel),
                output_amplifier,
                int(hsspeed_index),
                preamp_gain_index,
            )
            if int(ret) == int(self.errors.DRV_SUCCESS) and not int(available):
                raise AndorError(
                    "Preamp gain index "
                    f"{preamp_gain_index} is not available for AD channel "
                    f"{ad_channel}, output amplifier {output_amplifier}, "
                    f"HSSpeed index {hsspeed_index}"
                )

        self._check(sdk.SetPreAmpGain(preamp_gain_index), "SetPreAmpGain")

    def set_frame_transfer_mode(self, enable: bool) -> None:
        """Enable or disable frame-transfer acquisition mode."""
        if self.simulation:
            self._require_init()
            return
        self._check(
            self._sdk_required().SetFrameTransferMode(1 if enable else 0),
            "SetFrameTransferMode",
        )

    def set_shutter(
        self,
        mode: int | None = None,
        *,
        typ: int = 1,
        closing_ms: int = 50,
        opening_ms: int = 50,
        extmode: int = 1,
    ) -> None:
        """Configure the internal/external shutter modes and timing values."""
        if self.simulation:
            self._require_init()
            return
        if mode is None:
            mode = self.codes.Shutter_Mode.FULLY_AUTO
        self._check(
            self._sdk_required().SetShutterEx(
                int(typ),
                int(mode),
                int(closing_ms),
                int(opening_ms),
                int(extmode),
            ),
            "SetShutterEx",
        )

    def set_shutter_open_for_series(self) -> None:
        """Open the shutter for acquisition series and close it otherwise."""
        self.set_shutter(mode=self.codes.Shutter_Mode.OPEN_FOR_ANY_SERIES)

    def set_shutter_permanently_open(self) -> None:
        """Force the shutter permanently open for alignment or debugging."""
        self.set_shutter(mode=self.codes.Shutter_Mode.PERMANENTLY_OPEN)

    def set_shutter_permanently_closed(self) -> None:
        """Force the shutter permanently closed for background measurements."""
        self.set_shutter(mode=self.codes.Shutter_Mode.PERMANENTLY_CLOSED)

    def set_trigger_mode(self, mode: int) -> None:
        """Set the SDK trigger mode using an Andor Trigger_Mode enum value."""
        if self.simulation:
            self._require_init()
            return
        self._check(self._sdk_required().SetTriggerMode(int(mode)), "SetTriggerMode")

    def set_fast_ext_trigger(self, enable: bool) -> None:
        """Enable or disable Andor's fast external trigger handling."""
        if self.simulation:
            self._require_init()
            return
        self._check(
            self._sdk_required().SetFastExtTrigger(1 if enable else 0),
            "SetFastExtTrigger",
        )

    def set_exposure_time(self, exposure_s: float) -> None:
        """Set the software exposure time in seconds for timed modes."""
        if self.simulation:
            self._require_init()
            return
        self._check(
            self._sdk_required().SetExposureTime(float(exposure_s)),
            "SetExposureTime",
        )

    def set_image_region(
        self,
        x0: int,
        x1: int,
        y0: int,
        y1: int,
        hbin: int = 1,
        vbin: int = 1,
    ) -> None:
        """Set the zero-based inclusive image ROI and binning."""
        self._require_init()
        self._roi = (int(x0), int(x1), int(y0), int(y1))
        if self.simulation:
            return

        self._check(
            self._sdk_required().SetImage(
                int(hbin),
                int(vbin),
                int(x0) + 1,
                int(x1) + 1,
                int(y0) + 1,
                int(y1) + 1,
            ),
            "SetImage",
        )

    def prepare(self) -> None:
        """Ask the SDK to allocate/prepare buffers for the next acquisition.

        This reads the current acquisition setup and allocates/configures SDK
        memory. StartAcquisition will do this implicitly if needed, but calling
        PrepareAcquisition first can remove a long allocation/setup delay from
        StartAcquisition, especially for long kinetic series or iDus parameter
        changes.
        """
        if self.simulation:
            self._require_init()
            return
        self._check(self._sdk_required().PrepareAcquisition(), "PrepareAcquisition")

    def start_acquisition(self) -> None:
        """Start acquisition using the currently configured SDK mode."""
        if self.simulation:
            self._require_init()
            return
        self._check(self._sdk_required().StartAcquisition(), "StartAcquisition")

    def abort_acquisition(self, ignore_idle: bool = False) -> None:
        """Stop an active acquisition, optionally accepting an already-idle camera."""
        if self.simulation:
            self._require_init()
            return
        ignore = (int(self.errors.DRV_IDLE),) if ignore_idle else ()
        self._check(
            self._sdk_required().AbortAcquisition(),
            "AbortAcquisition",
            ignore=ignore,
        )

    def wait(self, timeout_ms: int | None = 5000) -> None:
        """Wait for the next acquisition event without reading image data."""
        if self.simulation:
            self._require_init()
            return

        if timeout_ms is None:
            ret = self._sdk_required().WaitForAcquisition()
        else:
            timeout_ms = int(timeout_ms)
            ret = self._sdk_required().WaitForAcquisitionTimeOut(timeout_ms)
        if int(ret) == int(self.errors.DRV_NO_NEW_DATA):
            self._raise_timeout("WaitForAcquisition", timeout_ms)
        self._check(ret, "WaitForAcquisition")

    def get_image16(self) -> np.ndarray:
        """Return the most recent image as a 2D uint16 array without arming."""
        self._require_init()
        if self.simulation:
            return self._simulation_images(1)[0]

        image_size = self._image_size()
        ret, buf = self._sdk_required().GetMostRecentImage16(image_size)
        self._check(ret, "GetMostRecentImage16")
        height, width = self._image_shape()
        return self._buffer_to_uint16(buf, image_size).reshape(height, width)

    # --------- ARTIQ-oriented acquisition helpers ---------

    def configure_external_exposure_single(
        self,
        roi: tuple[int, int, int, int] | None = None,
        hbin: int = 1,
        vbin: int = 1,
        fast_ext_trigger: bool = True,
    ) -> None:
        """Configure one externally gated bulb exposure for ARTIQ TTL control."""
        self.set_frame_transfer_mode(False)
        self.set_shutter_open_for_series()
        if not self.simulation:
            self._check(
                self._sdk_required().SetAcquisitionMode(
                    int(self.codes.Acquisition_Mode.SINGLE_SCAN)
                ),
                "SetAcquisitionMode(SINGLE_SCAN)",
            )
        self.set_trigger_mode(self.codes.Trigger_Mode.EXTERNAL_EXPOSURE_BULB)
        self.set_fast_ext_trigger(fast_ext_trigger)
        if roi is not None:
            self.set_image_region(*roi, hbin=hbin, vbin=vbin)

    def configure_external_exposure_run_till_abort(
        self,
        roi: tuple[int, int, int, int] | None = None,
        hbin: int = 1,
        vbin: int = 1,
        fast_ext_trigger: bool = False,
        exposure_time_s: float | None = None,
    ) -> None:
        """Configure externally gated bulb exposures until AbortAcquisition."""
        self.set_frame_transfer_mode(False)
        self.set_shutter_permanently_open()
        if not self.simulation:
            self._check(
                self._sdk_required().SetAcquisitionMode(
                    int(self.codes.Acquisition_Mode.RUN_TILL_ABORT)
                ),
                "SetAcquisitionMode(RUN_TILL_ABORT)",
            )
        self.set_trigger_mode(self.codes.Trigger_Mode.EXTERNAL_EXPOSURE_BULB)
        self.set_fast_ext_trigger(fast_ext_trigger)
        if exposure_time_s is not None:
            self.set_exposure_time(exposure_time_s)
        if roi is not None:
            self.set_image_region(*roi, hbin=hbin, vbin=vbin)

    def configure_external_exposure_series(
        self,
        n_images: int,
        roi: tuple[int, int, int, int] | None = None,
        hbin: int = 1,
        vbin: int = 1,
        fast_ext_trigger: bool = True,
        exposure_time_s: float | None = None,
        kinetic_cycle_time_s: float | None = None,
    ) -> None:
        """Configure a finite externally gated kinetic series of n_images frames."""
        if n_images <= 0:
            raise ValueError("n_images must be positive")

        self.set_frame_transfer_mode(False)
        self.set_shutter_open_for_series()
        if not self.simulation:
            sdk = self._sdk_required()
            self._check(
                sdk.SetAcquisitionMode(int(self.codes.Acquisition_Mode.KINETICS)),
                "SetAcquisitionMode(KINETICS)",
            )
            self._check(sdk.SetNumberKinetics(int(n_images)), "SetNumberKinetics")
        self.set_trigger_mode(self.codes.Trigger_Mode.EXTERNAL_EXPOSURE_BULB)
        self.set_fast_ext_trigger(fast_ext_trigger)
        if exposure_time_s is not None:
            self.set_exposure_time(exposure_time_s)
        if kinetic_cycle_time_s is not None and not self.simulation:
            self._check(
                self._sdk_required().SetKineticCycleTime(float(kinetic_cycle_time_s)),
                "SetKineticCycleTime",
            )
        if roi is not None:
            self.set_image_region(*roi, hbin=hbin, vbin=vbin)

    def start_external_exposure_single(
        self,
        roi: tuple[int, int, int, int] | None = None,
        hbin: int = 1,
        vbin: int = 1,
        configure: bool = True,
    ) -> None:
        """Optionally configure, then prepare and arm one external exposure."""
        if configure:
            self.configure_external_exposure_single(roi=roi, hbin=hbin, vbin=vbin)
        self.prepare()
        self.start_acquisition()

    def start_external_exposure_series(
        self,
        n_images: int,
        roi: tuple[int, int, int, int] | None = None,
        hbin: int = 1,
        vbin: int = 1,
        configure: bool = True,
        exposure_time_s: float | None = None,
        kinetic_cycle_time_s: float | None = None,
    ) -> None:
        """Optionally configure, then prepare and arm an external exposure series."""
        if configure:
            self.configure_external_exposure_series(
                n_images=n_images,
                roi=roi,
                hbin=hbin,
                vbin=vbin,
                exposure_time_s=exposure_time_s,
                kinetic_cycle_time_s=kinetic_cycle_time_s,
            )
        self.prepare()
        self.start_acquisition()

    def wait_get_image16(self, timeout_ms: int | None = 5000) -> np.ndarray:
        """Wait for an acquisition event, then return the most recent image."""
        self.wait(timeout_ms=timeout_ms)
        return self.get_image16()

    def wait_get_images16(self, n_images: int, timeout_ms: int | None = 5000) -> np.ndarray:
        """Wait for and drain n_images currently unread 16-bit images."""
        first, last = self._wait_for_new_images(int(n_images), timeout_ms)
        return self._read_images16(first, last)

    def get_new_images16(self, n_images: int | None = None) -> np.ndarray:
        """Drain available unread images, or exactly n_images if requested."""
        self._require_init()
        if self.simulation:
            return self._simulation_images(1 if n_images is None else int(n_images))

        count, first, _last = self._new_image_range()
        if count == 0:
            code = int(self.errors.DRV_NO_NEW_DATA)
            code_name = self._code_name(code)
            raise AndorError(f"No new images available: {code_name} ({code})", code, code_name)

        if n_images is None:
            n_images = count
        else:
            n_images = int(n_images)
            if n_images <= 0:
                raise ValueError("n_images must be positive")
            if count < n_images:
                raise AndorError(f"Only {count} new image(s) available, requested {n_images}")

        return self._read_images16(first, first + n_images - 1)
