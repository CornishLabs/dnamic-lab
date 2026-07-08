import matplotlib.pyplot as plt

# Data
asf = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.65, 0.7]
power_mw = [1.9, 7.2, 15.2, 23.2, 31.4, 38.1, 38.9, 38.0]
pd_voltage_mv = [63, 252, 562, 818, 1043, 1257, 1260, 1370]

fig, ax1 = plt.subplots(figsize=(7, 4.5))

# First y-axis: Power
line1, = ax1.plot(asf, power_mw, marker='o', label='Power before AOD',color='blue')
ax1.set_xlabel('Amplitude Scale Factor (ASF) @ 8 dB attenuation')
ax1.set_ylabel('Power before AOD (mW)')
ax1.grid(True, alpha=0.3)

# Second y-axis: PD voltage
ax2 = ax1.twinx()
line2, = ax2.plot(asf, pd_voltage_mv, marker='s', label='High-Z PD voltage',color='red')
ax2.set_ylabel('High-Z PD voltage on picoscope (mV)')

# Combined legend
lines = [line1, line2]
labels = [line.get_label() for line in lines]
ax1.legend(lines, labels, loc='upper left')

plt.title('Power and PD Voltage vs ASF')
plt.tight_layout()
plt.show()