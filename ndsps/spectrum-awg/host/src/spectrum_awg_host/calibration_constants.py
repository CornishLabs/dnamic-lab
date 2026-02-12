from awgsegmentfactory.calibration import AODSin2Calib, AWGPhysicalSetupInfo

lut: dict[str, AWGPhysicalSetupInfo] = {}

"""
uv run python -m awgsegmentfactory.tools.fit_optical_power_calibration \
--input-data-file ./examples/calibrations/814_H_calFile_17.02.2022_0\=0.txt \
--input-data-file ./examples/calibrations/814_V_calFile_17.02.2022_0\=0.txt \
--logical-to-hardware-map H=0 --logical-to-hardware-map V=1 \
--plot
"""
AWG_817_CALIB_CH0 = AODSin2Calib(
    g_poly_high_to_low=(0.4090989912253647, 0.2018618742515838, -0.9215927915167634, -0.4412476500516808, -0.1906036583947077, -0.03972136086542679, 1.177618710972786),
    v0_a_poly_high_to_low=(-206.7523109846387, -51.19251677219333, 328.8140960804296, 66.80024091181865, -142.5622869379855, -11.97940312739627, 199.7834400057718),
    freq_min_hz=80000000,
    freq_max_hz=120000000,
    traceability_string='examples/calibrations/814_H_calFile_17.02.2022_0=0.txt',
    min_g=1e-12,
    min_v0_sq=1e-09,
    y_eps=1e-06,
)
AWG_817_CALIB_CH1 = AODSin2Calib(
    g_poly_high_to_low=(0.1476077059082392, -0.03982795249054526, -0.6737338878355211, -0.02778152984507814, 0.3370885748323629, 0.02326850145597811, 0.9935230408830742),
    v0_a_poly_high_to_low=(-155.0879927769708, -49.22556216836927, 253.8100155943833, 63.78042793848495, -98.59810691776225, -12.31066136792399, 151.8124648021865),
    freq_min_hz=80000000,
    freq_max_hz=120000000,
    traceability_string='examples/calibrations/814_V_calFile_17.02.2022_0=0.txt',
    min_g=1e-12,
    min_v0_sq=1e-09,
    y_eps=1e-06,
)

AWG_817_CALIB = AWGPhysicalSetupInfo(
    logical_to_hardware_map={'H': 0, 'V': 1},
    channel_calibrations=(AWG_817_CALIB_CH0, AWG_817_CALIB_CH1),
)

# Add to the LUT
lut["AWG_817_CALIB"] = AWG_817_CALIB

"""
uv run python -m awgsegmentfactory.tools.fit_optical_power_calibration \
--input-data-file ./examples/calibrations/AWG1_calibration_22_02_2023_90MHz_255MHz.awgde \
--logical-to-hardware-map H=0 \
--plot
"""
AWG_938_CALIB_CH0 = AODSin2Calib(
    g_poly_high_to_low=(3.538826714549613, 5.312443088870038, -0.04736469375881174, -4.824428845570577, -4.545263097686731, 0.7659317524132674, 2.386968689643068),
    v0_a_poly_high_to_low=(-3904.371247207825, 553.4946879703813, 5822.427228628258, 209.2972708093259, -1189.579569913762, 49.98357276909121, 273.4502137609406),
    freq_min_hz=90000000,
    freq_max_hz=246500000,
    traceability_string='examples/calibrations/AWG1_calibration_22_02_2023_90MHz_255MHz.awgde',
    min_g=1e-12,
    min_v0_sq=1e-09,
    y_eps=1e-06,
)
AWG_938_CALIB = AWGPhysicalSetupInfo(
    logical_to_hardware_map={'H': 0},
    channel_calibrations=(AWG_938_CALIB_CH0,),
)

# Add to the LUT
lut["AWG_938_CALIB"] = AWG_938_CALIB

"""
uv run python -m awgsegmentfactory.tools.fit_optical_power_calibration \
--input-data-file ./examples/calibrations/AWG3_calibration_04_10_2024_98MHz_118MHz.awgde \
--logical-to-hardware-map H=0 \
--plot
"""
AWG_1145_CALIB_CH0 = AODSin2Calib(
    g_poly_high_to_low=(-0.8754904123674585, 0.01908417521116575, 2.746368559542769, 0.354430567446119, -3.0483222669169, -0.5073342883236134, 1.384480548700198),
    v0_a_poly_high_to_low=(195.3448675907807, 116.7738888023813, -242.9455379699489, -166.6070725616716, 26.10215046803684, 66.14371171486478, 376.4358129178906),
    freq_min_hz=85000000,
    freq_max_hz=135000000,
    traceability_string='examples/calibrations/AWG3_calibration_04_10_2024_98MHz_118MHz.awgde',
    min_g=1e-12,
    min_v0_sq=1e-09,
    y_eps=1e-06,
)
AWG_1145_CALIB = AWGPhysicalSetupInfo(
    logical_to_hardware_map={'H': 0},
    channel_calibrations=(AWG_1145_CALIB_CH0,),
)

lut["AWG_1145_CALIB"] = AWG_1145_CALIB
