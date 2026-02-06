# Spectrum AWG NDSP

This NDSP is used for programming the sequence mode of a Spectrum AWG card.
The builder architechture of the `AWGSegmentFactory` library is used on the Artiq side, this is then passed through various levels of intermediate representation, then passed to this NDSP for compilation and upload to the actual card.