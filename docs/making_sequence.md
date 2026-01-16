# Making a sequence note

For simple sequences, can just use vanilla Artiq's `EnvExperiment`. But for making parts that will be reused
within the experiment in multiple places, it's best to make it an `ndscan` `ExpFragment` to make it composible.

I haven't looked yet but I assume that the best place to make debugging experiments is with the interactive args.