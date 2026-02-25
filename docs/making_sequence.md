# Making a sequence note

For simple sequences, can just use vanilla Artiq's `EnvExperiment`. But for making parts that will be reused
within the experiment in multiple places, it's best to make it an `ndscan` `ExpFragment` to make it composible.

I haven't looked yet but I assume that the best place to make debugging experiments is with the interactive args.

- Use the `constants.py` file for defaults, try make the datastructures nice so they can be reused.
- Use the datasets feature of artiq to store live servo params (i.e. if we have regular calibration scans).
These can be loaded in as NDScan defaults.
- On every timeline stamp in a fragment, add a note on what is expected to be initialised, and whether things can be done more than once in a sequence.