from ndscan.experiment import (
    ExpFragment, kernel, rpc,
    FloatParam, IntParam,
    IntChannel, FloatChannel, OpaqueChannel,
    MHz, us, ms, A,
    make_fragment_scan_exp, CustomAnalysis, annotations,
    SubscanExpFragment, ScanOptions, LinearGenerator
)

import oitg
from repository.simple_examples.sequence_composition.atom_MW_state_change import (
    MultiShotAnalysed,
)

class ScanFrequency(SubscanExpFragment):
    def build_fragment(self):
        self.setattr_fragment("one_p", MultiShotAnalysed)
        super().build_fragment(self, "one_p", [(self.one_p.carrier.shot.pulse, "frequency")])

    def _configure(self):
        gen  = LinearGenerator(5*MHz, 15*MHz, 20, True)
        opts = ScanOptions(num_repeats=1, num_repeats_per_point=1, randomise_order_globally=True)
        self.configure([(self.one_p.carrier.shot.pulse.frequency, gen)], options=opts) 

    def host_setup(self):
        self._configure()
        super().host_setup()

    def device_setup(self):
        self._configure()
        self.device_setup_subfragments()

    def get_default_analyses(self):
        return [
            CustomAnalysis([self.one_p.carrier.shot.coil_current], self._analyse_field_calibration, [
                OpaqueChannel("f0_fit_xs"),
                OpaqueChannel("f0_fit_ys"),
                FloatChannel("f00", "Zero Field f0", unit="MHz", display_hints={"priority":-1}),
                FloatChannel("f00_err", "Zero Field f0 error", unit="MHz", display_hints={"error_bar_for": "f00", "priority":-1}),
                FloatChannel("f0_MHz_shift_per_A", "f0 shift per coil A", unit="MHz/A", scale=1e6, display_hints={"priority":-1}),
                FloatChannel("f0_MHz_shift_per_A_err", "f0 shift per coil A error", unit="MHz/A", scale=1e6, display_hints={"error_bar_for": "f0_MHz_shift_per_A", "priority":-1}),
            ])
        ]

    def _analyse_field_calibration(self, axis_values, result_values, analysis_results):
        x = axis_values[self.one_p.carrier.shot.coil_current]
        y = result_values[self._f0]
        y_err = result_values[self._f0_err]
        
        fit_results, fit_errs, fit_xs, fit_ys = oitg.fitting.line.fit(x, y, y_err, evaluate_function=True, evaluate_n=10)

        analysis_results["f0_fit_xs"].push(fit_xs)
        analysis_results["f0_fit_ys"].push(fit_ys)
        analysis_results["f00"].push(fit_results["a"])
        analysis_results["f00_err"].push(fit_errs["a"])
        analysis_results["f0_MHz_shift_per_A"].push(fit_results["b"])
        analysis_results["f0_MHz_shift_per_A_err"].push(fit_errs["b"])

        self.set_dataset("f00", fit_results["a"], broadcast=True, unit="MHz")

        return [
            annotations.curve_1d(x_axis=self.one_p.carrier.shot.coil_current,
                                 x_values=fit_xs,
                                 y_axis=self._f0,
                                 y_values=fit_ys)
        ]


class ScanField(SubscanExpFragment):
    def build_fragment(self):
        self.setattr_fragment("scan_f", ScanFrequency)
        super().build_fragment(self, "scan_f", [(self.scan_f.one_p.carrier.shot, "coil_current")])

    def _configure(self):
        gen  = LinearGenerator(0*A, 10*A, 6, True)
        opts = ScanOptions(num_repeats=1, num_repeats_per_point=1, randomise_order_globally=True)
        self.configure([(self.scan_f.one_p.carrier.shot.coil_current, gen)], options=opts) 

    def host_setup(self):
        self._configure()
        super().host_setup()

    def device_setup(self):
        self._configure()
        self.device_setup_subfragments()


CalibrateFieldExperiment = make_fragment_scan_exp(ScanField)
