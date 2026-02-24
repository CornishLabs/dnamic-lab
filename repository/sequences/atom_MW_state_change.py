from ndscan.experiment import (
    ExpFragment, kernel, rpc,
    FloatParam, IntParam,
    IntChannel, FloatChannel, OpaqueChannel,
    MHz, us, ms, A,
    make_fragment_scan_exp, CustomAnalysis, annotations
)
import oitg
import math

from repository.models.atom_response import p_bright_detuned_rabi
from repository.components import PrepareAtom, Pulse, ReadoutFluorescence

from repository.lib.make_shot_scan import make_shot_chunk_exp_fragments_from_shot
from repository.lib.single_shot_base import SingleShotBase

class OneShot(SingleShotBase):
    def build_fragment(self):
        self.setattr_fragment("prep",  PrepareAtom)
        self.setattr_fragment("pulse", Pulse)
        self.setattr_fragment("ro",    ReadoutFluorescence)

        # Lineshape parameters
        self.setattr_param("coil_current",  FloatParam, "Coil Current",    default=0*A, unit="A")
        self.setattr_param("rabi_freq",     FloatParam, "Rabi frequency",  default=1*MHz, unit="MHz", min=0.0)

        # Efficient handle to set p_bright
        _, self._pb_store = self.ro.override_param("p_bright")

    def run_once(self):
        self.prep.run_once()
        self.pulse.run_once()

        # Simulate atom response (state -> p_bright)
        pb = p_bright_detuned_rabi(
            self.pulse.frequency.get(),        # Hz
            self.coil_current.get(),     # Hz
            self.rabi_freq.get(),              # Hz
            self.pulse.duration.get(),         # seconds
        )
        self._pb_store.set_value(pb)

        self.ro.run_once()

    # def get_classification_handle(self):
    #     return self.ro.is_bright_class

    def get_counts_handle(self):
        return self.ro.counts


OneShotCarrier, MultiShot = make_shot_chunk_exp_fragments_from_shot(OneShot)

class MultiShotAnalysed(MultiShot):
    def get_default_analyses(self):
        return [
            CustomAnalysis([self.carrier.shot.pulse.duration], self._analyse_time_scan, [
                OpaqueChannel("t_pi_fit_xs"),
                OpaqueChannel("t_pi_fit_ys"),
                FloatChannel("t_pi", "Fitted π time", unit="us"),
                FloatChannel("t_pi_err", "Fitted π time error", unit="us", display_hints={"error_bar_for": "_t_pi"})
            ]),
            CustomAnalysis([self.carrier.shot.pulse.frequency], self._analyse_frequency_scan, [
                OpaqueChannel("f0_fit_xs"),
                OpaqueChannel("f0_fit_ys"),
                FloatChannel("f0", "Fitted centre frequency", unit="MHz"),
                FloatChannel("f0_err", "Fitted centre frequency error", unit="MHz", display_hints={"error_bar_for": "_f0"})
            ])
        ]

    def _analyse_time_scan(self, axis_values, result_values, analysis_results):
        x = axis_values[self.carrier.shot.pulse.duration]
        # See: https://oxfordiontrapgroup.github.io/ndscan/apidocs.html#module-ndscan.experiment.default_analysis
        # Conceptually, analyses are attached to a fragment, and produce results “the next level up” 
        #    – that is, they condense all the points from a scan over a particular choice of parameters into a few derived results.
        y = result_values[self._GaR0_p]
        y_err = result_values[self._GaR0_p_avg_err]

        fit_results, fit_errs, fit_xs, fit_ys = oitg.fitting.sinusoid.fit(
            x, y, y_err, evaluate_function=True, evaluate_n=200)

        analysis_results["t_pi"].push(fit_results["t_pi"]-fit_results["t_dead"])
        analysis_results["t_pi_err"].push(fit_errs["t_pi"])
        analysis_results["t_pi_fit_xs"].push(fit_xs)
        analysis_results["t_pi_fit_ys"].push(fit_ys)

        # We can also return custom annotations to be displayed, which can make use of
        # the analysis results.
        return [
            annotations.axis_location(axis=self.carrier.shot.pulse.duration,
                                      position=fit_results["t_pi"]-fit_results["t_dead"],
                                      position_error=fit_errs["t_pi"]),
            annotations.curve_1d(x_axis=self.carrier.shot.pulse.duration,
                                 x_values=fit_xs,
                                 y_axis=self._GaR0_p,
                                 y_values=fit_ys)
        ]
    
    def _analyse_frequency_scan(self, axis_values, result_values, analysis_results):
        x = axis_values[self.carrier.shot.pulse.frequency]
        y = result_values[self._GaR0_p]
        y_err = result_values[self._GaR0_p_avg_err]

        fit_results, fit_errs, fit_xs, fit_ys = oitg.fitting.sinc_2.fit(
            x, y, y_err, evaluate_function=True, evaluate_n=200, initialise = {'y0':0.0, 'a': 1.0, 'width': 1e6})

        analysis_results["f0"].push(fit_results["x0"])
        analysis_results["f0_err"].push(fit_errs["x0"])
        analysis_results["f0_fit_xs"].push(fit_xs)
        analysis_results["f0_fit_ys"].push(fit_ys)


        return [
            annotations.axis_location(axis=self.carrier.shot.pulse.frequency,
                                      position=fit_results["x0"],
                                      position_error=fit_errs["x0"]),
            annotations.curve_1d(x_axis=self.carrier.shot.pulse.frequency,
                                 x_values=fit_xs,
                                 y_axis=self._GaR0_p,
                                 y_values=fit_ys)
        ]

MultiShotExperiment = make_fragment_scan_exp(MultiShotAnalysed)
