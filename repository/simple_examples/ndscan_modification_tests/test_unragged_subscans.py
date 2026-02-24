# Minimal ragged subscan demo (host-only, no core device needed)
# Drop this file somewhere your ARTIQ/ndscan experiment explorer can see it.

from ndscan.experiment import (
    ExpFragment,
    SubscanExpFragment,
    FloatParam,
    IntParam,
    ListGenerator,
    make_fragment_scan_exp,
    LinearGenerator,
    ScanOptions,
    CustomAnalysis,
    FloatChannel,
    annotations,
    OpaqueChannel,
)

import numpy as np
from scipy.optimize import curve_fit


class LineExp(ExpFragment):
    def build_fragment(self):
        self.setattr_param("p", FloatParam, "p", default=2.0)
        self.setattr_param("x", FloatParam, "x", default=0.0)
        self.setattr_result("y")  # Float channel by default

    def run_once(self):
        x = self.x.use()
        m = self.p.use() ** 2  # This is the unknown functional form of how m varies
        self.y.push(m * x)  # simple deterministic number

    def get_default_analyses(self):
        return [
            CustomAnalysis(
                [self.x],
                self._analyse_grad,
                [
                    OpaqueChannel("fit_xs"),
                    OpaqueChannel("fit_ys"),
                    FloatChannel("m", "extracted m"),
                ],
            )
        ]

    def _analyse_grad(self, axis_values, result_values, analysis_results):
        x = axis_values[self.x]
        y = result_values[self.y]

        def model(x, m):
            return m * x

        popt, pcov = curve_fit(model, x, y)
        m = popt[0]

        fit_xs = np.linspace(np.min(x), np.max(x), 20)
        fit_ys = m * fit_xs

        analysis_results["fit_xs"].push(fit_xs)
        analysis_results["fit_ys"].push(fit_ys)
        analysis_results["m"].push(m)

        return [
            annotations.curve_1d(
                x_axis=self.x, x_values=fit_xs, y_axis=self.y, y_values=fit_ys
            )
        ]


class ScanXExpFrag(SubscanExpFragment):
    def build_fragment(self):
        self.setattr_fragment("lineexp", LineExp)
        super().build_fragment(self, "lineexp", [(self.lineexp, "x")])

    def _configure(self): # THIS IS UNRAGGED
        n = 6
        gen = LinearGenerator(0.0, 5.0, n, False)
        opts = ScanOptions(
            num_repeats=1, num_repeats_per_point=1, randomise_order_globally=False
        )
        self.configure([(self.lineexp.x, gen)], options=opts)

    def host_setup(self):
        self._configure()
        super().host_setup()

    def device_setup(self):
        self._configure()
        self.device_setup_subfragments()


class HowDoesPVaryExpFrag(SubscanExpFragment):
    def build_fragment(self):
        self.setattr_fragment("scanxexp", ScanXExpFrag)
        super().build_fragment(self, "scanxexp", [(self.scanxexp.lineexp, "p")])

    def _configure(self):
        gen = LinearGenerator(0.0, 5.0, 10, False)
        opts = ScanOptions(
            num_repeats=1, num_repeats_per_point=1, randomise_order_globally=False
        )
        self.configure([(self.scanxexp.lineexp.p, gen)], options=opts)

    def host_setup(self):
        self._configure()
        super().host_setup()

    def device_setup(self):
        self._configure()
        self.device_setup_subfragments()


UNRaggedHowDoesPVaryExp = make_fragment_scan_exp(HowDoesPVaryExpFrag)
