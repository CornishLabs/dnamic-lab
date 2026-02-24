# =========================
# Vanilla ARTIQ tree demo
# =========================
from artiq.experiment import EnvExperiment, kernel, delay, ms

class TreeDemo(EnvExperiment):
    """
    A minimal ARTIQ experiment showing:
      - build/prepare/run/analyze lifecycle
      - where you'd get devices, datasets, arguments
      - how you'd structure host vs kernel calls
    The 'run()' prints a tree of steps instead of using hardware.
    """

    def build(self):
        # --- Devices you control (core device is typical)
        self.setattr_device("core")
        # self.setattr_device("ttl0")         # example I/O
        # self.setattr_device("dataset_mgr")  # conceptual (datasets via self.set_dataset)
        #
        # --- Arguments you might expose to the scheduler/dashboard
        # self.setattr_argument("num_shots", NumberValue(100, step=1, min=1))

    def prepare(self):
        # --- Host-side preparation (waveforms, datasets, calibration tables, …)
        # self.set_dataset("config/example", {"mode": "demo"})
        pass

    def run(self):
        # --- Host orchestrates the flow; kernels are called for timing-critical parts
        self._p(0, "ROOT")
        self._setup()

        self._p(1, "Branch A")
        self._branch_a()

        self._p(1, "Branch B")
        self._branch_b()

    def analyze(self):
        # --- Host-side post-processing/plotting/saving
        self._p(0, "ANALYZE (host)")

    # ------------------------------
    # Helpers – host orchestration
    # ------------------------------
    def _setup(self):
        self._p(1, "Setup (host)")
        # If you needed the device, you'd call a kernel:
        self.k_setup()

    def _branch_a(self):
        self._p(2, "A1 (host)")
        # self.k_do_thing_A1()
        self._p(2, "A2 (host)")
        # self.k_do_thing_A2()

    def _branch_b(self):
        self._p(2, "B1 (host)")
        # self.k_do_thing_B1()

    # ------------------------------
    # Kernels – device-side actions
    # (Here we keep them as stubs with comments)
    # ------------------------------
    @kernel
    def k_setup(self):
        # self.core.reset()
        # delay(1*ms)
        pass

    @kernel
    def k_do_thing_A1(self):
        # precise I/O/timing here (TTL, DDS, FPGA, …)
        # delay(0.5*ms)
        pass

    @kernel
    def k_do_thing_A2(self):
        pass

    @kernel
    def k_do_thing_B1(self):
        pass

    # ------------------------------
    # Pretty printing for the tree
    # ------------------------------
    def _p(self, level: int, msg: str):
        print("  " * level + f"- {msg}")
