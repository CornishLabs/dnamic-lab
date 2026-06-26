from ndscan.experiment import (
    ExpFragment, SubscanExpFragment,
    IntParam, LinearGenerator, ScanOptions,
    CustomAnalysis, FloatChannel, make_fragment_scan_exp, IntChannel
)
import math
import numpy as np
from repository.lib.stats import jeffreys_median_ci, moment_matched_beta_for_average, beta_quartiles, pooled_posterior_beta

def make_shot_indexed_carrier(ShotCls):
    """Return a concrete carrier class wrapping `ShotCls` and owning `shot_index` + analysis."""
    class ShotCarrier(ExpFragment):
        def build_fragment(self):
            # axis lives HERE (so analysis can be here too)
            self.setattr_param("shot_index", IntParam, "Shot index", default=0, is_scannable=True)
            self.setattr_fragment("shot", ShotCls)

        def run_once(self):
            self.shot.run_once()

        # Summarise the N shots → p ± err (uses child’s declared handles)
        def get_default_analyses(self):
            rois = self.get_dataset("rois")

            channels = []
            for roi_i in range(len(rois[0])):
                pre_key_avg = f"GaR{roi_i}"
                channels.append(FloatChannel(f"{pre_key_avg}_p", f"{pre_key_avg} bright prob", display_hints={"priority": 4}))
                channels.append(FloatChannel(f"{pre_key_avg}_p_upper_err", f"{pre_key_avg} bright prob upper error", display_hints={"priority": -2}))
                channels.append(FloatChannel(f"{pre_key_avg}_p_lower_err", f"{pre_key_avg} bright prob lower error", display_hints={"priority": -2}))
                channels.append(FloatChannel(f"{pre_key_avg}_p_avg_err", f"{pre_key_avg} bright prob avg error",display_hints={"error_bar_for": f"_{pre_key_avg}_p"}))
                # channels.append(IntChannel(f"{pre_key_avg}_n", f"{pre_key_avg} number of shots", display_hints={"priority": -2}))
                # channels.append(IntChannel(f"{pre_key_avg}_y", f"{pre_key_avg} number of bright shots", display_hints={"priority": -2}))
                for gi in range(len(rois)):
                    pre_key = f"G{gi}R{roi_i}"
                    channels.append(FloatChannel(f"{pre_key}_p", f"{pre_key} bright prob", display_hints={"priority": 2, "share_axis_with": f"_G0R{roi_i}_p"}))
                    channels.append(FloatChannel(f"{pre_key}_p_upper_err", f"{pre_key} bright prob upper error", display_hints={"priority": -2}))
                    channels.append(FloatChannel(f"{pre_key}_p_lower_err", f"{pre_key} bright prob lower error", display_hints={"priority": -2}))
                    channels.append(FloatChannel(f"{pre_key}_p_avg_err", f"{pre_key} bright prob avg error",display_hints={"error_bar_for": f"_{pre_key}_p"}))
                    channels.append(IntChannel(f"{pre_key}_n", f"{pre_key} number of shots", display_hints={"priority": -2}))
                    channels.append(IntChannel(f"{pre_key}_y", f"{pre_key} number of bright shots", display_hints={"priority": -2}))
            return [
                CustomAnalysis(
                    [self.shot_index],
                    self._analyse_shots_to_p,
                    channels,
                )
            ]

        def _analyse_shots_to_p(self, axis_values, result_values, analysis_results):
            classes_handle = self.shot.get_counts_handle()
            counts = np.asarray(result_values[classes_handle])
            threshold = self.get_dataset("threshold",default=2000)
            rois = self.get_dataset("rois")
            
            n=np.sum(np.ones_like(counts),axis=0)
            y=np.sum(counts>threshold,axis=0)

            med,low,high = jeffreys_median_ci(y, n)
            rel_upper_err = high-med
            rel_low_err = med-low
            rel_avg_err=(high-low)/2
            
            for roi_i in range(len(rois[0])):
                pre_key_avg = f"GaR{roi_i}"
                for gi in range(len(rois)):
                    pre_key = f"G{gi}R{roi_i}"
                    analysis_results[f"{pre_key}_p"].push(med[gi,roi_i])
                    analysis_results[f"{pre_key}_p_upper_err"].push(rel_upper_err[gi,roi_i])
                    analysis_results[f"{pre_key}_p_lower_err"].push(rel_low_err[gi,roi_i])
                    analysis_results[f"{pre_key}_p_avg_err"].push(rel_avg_err[gi,roi_i])
                    analysis_results[f"{pre_key}_n"].push(n[gi,roi_i])
                    analysis_results[f"{pre_key}_y"].push(y[gi,roi_i])
                a_star, b_star= pooled_posterior_beta(y[:,roi_i],n[:,roi_i])#, drift_aware=True)
                lower_all,med_all,upper_all = beta_quartiles(a_star, b_star)
                print(lower_all,med_all,upper_all)
                rel_lower_all = med_all-lower_all
                rel_upper_all = upper_all - med_all
                rel_avg_all = (upper_all-lower_all)/2
                analysis_results[f"{pre_key_avg}_p"].push(med_all)
                analysis_results[f"{pre_key_avg}_p_upper_err"].push(rel_upper_all)
                analysis_results[f"{pre_key_avg}_p_lower_err"].push(rel_lower_all)
                analysis_results[f"{pre_key_avg}_p_avg_err"].push(rel_avg_all)
                # analysis_results[f"{pre_key_avg}_n"].push(n[gi,roi_i])
                # analysis_results[f"{pre_key_avg}_y"].push(y[gi,roi_i])


            return []

    return ShotCarrier


def make_shot_chunk_exp_fragments_from_shot(ShotCls, *, default_shots_per_chunk=40):
    Carrier = make_shot_indexed_carrier(ShotCls)

    # class ShotScan(SubscanExpFragment):
    #     pass

    class ShotChunk(SubscanExpFragment):
        def build_fragment(self):
            self.setattr_fragment("carrier", Carrier)
            
            # IF I have the empty class above doing the scan then
            # self.setattr_fragment("shot_scan", ShotScan, self, "carrier", [(self.carrier, "shot_index")])
            # ELSE (Then I have this class owning the scanning)
            super().build_fragment(self, "carrier", [(self.carrier, "shot_index")])

            self.setattr_param("shots_per_chunk", IntParam, "Shots per chunk",
                               default=default_shots_per_chunk, min=1)

        def _configure(self):
            # NOTE: Perhaps there's a performance opt. with checking `self.shots_per_chunk.changed_after_use()`?
            N = self.shots_per_chunk.get()
            gen  = LinearGenerator(0, N-1, N, True)
            opts = ScanOptions(num_repeats=1, num_repeats_per_point=1, randomise_order_globally=False)
            self.configure([(self.carrier.shot_index, gen)], options=opts)  # self.shot_scan.configure if using empty class

        def host_setup(self):
            self._configure()
            super().host_setup()

        def device_setup(self):
            self._configure()
            self.device_setup_subfragments()

        # IF using empty class
        # def run_once(self):
        #     self.shot_scan.run_once()

    return Carrier, ShotChunk