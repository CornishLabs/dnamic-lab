import threading
from typing import Optional, Tuple

import numpy as np
from pyAndorSDK2 import atmcd, atmcd_codes, atmcd_errors


class AndorError(RuntimeError):
    pass


class AndorEMCCD:
    """
    Minimal Andor EMCCD driver suitable as a SiPyCo RPC target.

    Design goals:
      - initialize once, keep connection open (keep cooler on)
      - lock all SDK calls
      - expose a small API similar to common ARTIQ experiment usage
    """

    def __init__(self, config_path: str = "/usr/local/etc/andor", simulation: bool = False):
        self.simulation = bool(simulation)
        self.config_path = config_path

        self.codes = atmcd_codes
        self.errors = atmcd_errors.Error_Codes

        self._lock = threading.RLock()
        self._sdk = None
        self._initialized = False

        # cached detector size
        self._xpixels: Optional[int] = None
        self._ypixels: Optional[int] = None

        # cached ROI in "ARTIQ-style" 0-based inclusive bounds: x0,x1,y0,y1
        self._roi = (0, 511, 0, 511)

    # --------- utilities ---------

    def ping(self) -> bool:
        # ctlmgr uses ping(). You can make this stronger by calling GetTemperature().
        return True

    def _check(self, ret: int, where: str) -> None:
        if ret != self.errors.DRV_SUCCESS:
            raise AndorError(f"{where} failed: code={ret}")

    def _require_init(self) -> None:
        if not self._initialized:
            raise AndorError("Camera not initialized (controller should call connect() at startup)")

    # --------- lifecycle ---------

    def connect(self) -> None:
        with self._lock:
            if self._initialized:
                return

            if self.simulation:
                self._initialized = True
                self._xpixels, self._ypixels = 512, 512
                return

            self._sdk = atmcd("")  # loads libandor.so
            ret = self._sdk.Initialize(self.config_path)
            self._check(ret, "Initialize")

            # cache detector size
            ret, xp, yp = self._sdk.GetDetector()
            self._check(ret, "GetDetector")
            self._xpixels, self._ypixels = int(xp), int(yp)

            # some sane defaults; adjust to taste
            ret = self._sdk.SetReadMode(self.codes.Read_Mode.IMAGE)
            self._check(ret, "SetReadMode(IMAGE)")

            ret = self._sdk.SetAcquisitionMode(self.codes.Acquisition_Mode.SINGLE_SCAN)
            self._check(ret, "SetAcquisitionMode(SINGLE_SCAN)")

            self._initialized = True

    def close(self) -> None:
        with self._lock:
            if not self._initialized:
                return
            try:
                if not self.simulation and self._sdk is not None:
                    # You may want to leave the cooler on until shutdown;
                    # since this is controller shutdown, it's OK to shut down the SDK.
                    ret = self._sdk.ShutDown()
                    self._check(ret, "ShutDown")
            finally:
                self._sdk = None
                self._initialized = False

    # --------- info/telemetry ---------

    def get_serial(self) -> int:
        with self._lock:
            self._require_init()
            if self.simulation:
                return 0
            ret, sn = self._sdk.GetCameraSerialNumber()
            self._check(ret, "GetCameraSerialNumber")
            return int(sn)

    def get_detector(self) -> Tuple[int, int]:
        with self._lock:
            self._require_init()
            return int(self._xpixels), int(self._ypixels)

    def cooler_on(self) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.CoolerON()
            self._check(ret, "CoolerON")

    def set_temperature(self, temp_c: int) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.SetTemperature(int(temp_c))
            self._check(ret, "SetTemperature")

    def get_temperature(self) -> Tuple[int, int]:
        """
        Returns (status_code, temp_c). Status code is Andor's return code.
        """
        with self._lock:
            self._require_init()
            if self.simulation:
                return (int(self.errors.DRV_SUCCESS), -75)
            ret, temp = self._sdk.GetTemperature()
            # Many users don't treat non-success as "fatal" here (e.g. temp not stabilized),
            # so we return the code rather than raising.
            return int(ret), int(temp)

    # --------- configuration ---------

    def set_frame_transfer_mode(self, enable: bool) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.SetFrameTransferMode(1 if enable else 0)
            self._check(ret, "SetFrameTransferMode")

    def set_shutter(self,
                    typ: int = 1,
                    mode: int = None,
                    closing_ms: int = 50,
                    opening_ms: int = 50,
                    extmode: int = 1) -> None:
        """
        Wrap SetShutterEx with sensible defaults.
        mode default: FULLY_AUTO (internal shutter auto)
        """
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            if mode is None:
                mode = self.codes.Shutter_Mode.FULLY_AUTO
            ret = self._sdk.SetShutterEx(int(typ), int(mode), int(closing_ms), int(opening_ms), int(extmode))
            self._check(ret, "SetShutterEx")

    def set_trigger_mode(self, mode: int) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.SetTriggerMode(int(mode))
            self._check(ret, "SetTriggerMode")

    def set_exposure_time(self, exposure_s: float) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.SetExposureTime(float(exposure_s))
            self._check(ret, "SetExposureTime")

    def set_image_region(self, x0: int, x1: int, y0: int, y1: int, hbin: int = 1, vbin: int = 1) -> None:
        """
        ARTIQ-style ROI: 0-based inclusive bounds.
        Andor SetImage uses 1-based inclusive: hstart/hend/vstart/vend.
        """
        with self._lock:
            self._require_init()
            self._roi = (int(x0), int(x1), int(y0), int(y1))

            if self.simulation:
                return

            # convert to 1-based inclusive
            hstart = int(x0) + 1
            hend = int(x1) + 1
            vstart = int(y0) + 1
            vend = int(y1) + 1

            ret = self._sdk.SetImage(int(hbin), int(vbin), hstart, hend, vstart, vend)
            self._check(ret, "SetImage")

    def prepare(self) -> None:
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.PrepareAcquisition()
            self._check(ret, "PrepareAcquisition")

    # --------- acquisition ---------

    def start_acquisition(self) -> None:
        """
        Arms the camera for SINGLE_SCAN. For external trigger modes, this typically waits for trigger.
        """
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.StartAcquisition()
            self._check(ret, "StartAcquisition")

    def wait(self) -> None:
        """
        Blocks until acquisition completes.
        In production you may prefer a timeout-based wait to avoid deadlocks if triggers stop.
        """
        with self._lock:
            self._require_init()
            if self.simulation:
                return
            ret = self._sdk.WaitForAcquisition()
            self._check(ret, "WaitForAcquisition")

    def get_image16(self) -> np.ndarray:
        """
        Returns the most recent acquired image as uint16 ndarray (H, W) for current ROI.

        Note: Andor returns a flat buffer; we reshape based on ROI.
        """
        with self._lock:
            self._require_init()

            x0, x1, y0, y1 = self._roi
            width = (x1 - x0 + 1)
            height = (y1 - y0 + 1)

            if self.simulation:
                # simple deterministic test pattern
                arr = (np.arange(width * height, dtype=np.uint16).reshape(height, width))
                return arr

            image_size = width * height
            ret, buf, validfirst, validlast = self._sdk.GetImages16(1, 1, image_size)
            self._check(ret, "GetImages16")

            # buf might be a list/array-like. Convert to numpy.
            # If buf supports the buffer protocol, np.frombuffer(buf, ...) is cheaper.
            try:
                img = np.frombuffer(buf, dtype=np.uint16, count=image_size).copy()
            except TypeError:
                img = np.array(buf, dtype=np.uint16, copy=True)

            img = img.reshape(height, width)
            return img

    def acquire_one(self) -> np.ndarray:
        """
        Convenience: prepare -> start -> wait -> read.
        For external exposure/trigger modes, StartAcquisition arms and WaitForAcquisition blocks until trigger+readout.
        """
        with self._lock:
            self.prepare()
            self.start_acquisition()
            self.wait()
            return self.get_image16()
