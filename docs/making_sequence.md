# Making a sequence note

For simple sequences, can just use vanilla Artiq's `EnvExperiment`. But for making parts that will be reused
within the experiment in multiple places, it's best to make it an `ndscan` `ExpFragment` to make it composible.

I haven't looked yet but I assume that the best place to make debugging experiments is with the interactive args.

- Use the `constants.py` file for defaults, try make the datastructures nice so they can be reused.
- Use the datasets feature of artiq to store live servo params (i.e. if we have regular calibration scans).
These can be loaded in as NDScan defaults.
- On every timeline stamp in a fragment, add a note on what is expected to be initialised, and whether things can be done more than once in a sequence.

Stuff that can't be scanned should go in host_setup, can can happily use dictionaries, but stuff that can be scanned should end up in a list in the kernel, and should be updated in the kernel. 

It should go in device_setup() if it's latency sensitive between steps, or in the fragment kernels the user runs if this latency penaltiy mid sequience isn't a problem. i.e. in the sequence you might want to do a then b quickly after.

If I'm scanning a parameter in b that takes a while to setup it's better to do setupb->a->b rather than a->setup_b->b . Also, it would be good to make use of the changed_after_use that would allow you to not resetup hardware that could be expensive to setup if it's not being scanned (static). I imagine this requires one to write lists of parameters that if any one of them (logical ORd) are changed_after_use then do the setup. For edxample, we might have a list of paramhandles that are for the RAM parameters, and in that case you would redo the RAM upload if any of them had changed. I prefer the kernel scan runner if possible.