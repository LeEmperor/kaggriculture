from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")

import matplotlib.pyplot as plt

PLOT_PATH = Path(__file__).with_name("plot.png")
PLOT_TEMP_PATH = Path(__file__).with_name(".plot.png.tmp")

x_axis1 = np.linspace(0, 100, 100)
y_axis = np.linspace(0, 100, 100)

figure, axes = plt.subplots()
axes.plot(x_axis1, y_axis)
axes.set_title("Kaggriculture bruh3 model")
axes.set_xlabel("x")
axes.set_ylabel("y")

# Replace the displayed file atomically so Emacs never reloads a partially
# written PNG while Auto Revert mode is watching it.
figure.savefig(
    PLOT_TEMP_PATH,
    format="png",
    dpi=160,
    bbox_inches="tight",
)
PLOT_TEMP_PATH.replace(PLOT_PATH)
plt.close(figure)
