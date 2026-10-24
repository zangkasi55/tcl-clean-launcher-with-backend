package com.gathin.tvhome;

final class ThemeStyle {
    final int gradientColor, gradientOpacity;
    final int clockColor, dateColor, inputColor, wifiColor, settingsColor;

    ThemeStyle(int gradientColor, int gradientOpacity, int clockColor, int dateColor,
               int inputColor, int wifiColor, int settingsColor) {
        this.gradientColor = gradientColor;
        this.gradientOpacity = Math.max(0, Math.min(100, gradientOpacity));
        this.clockColor = clockColor;
        this.dateColor = dateColor;
        this.inputColor = inputColor;
        this.wifiColor = wifiColor;
        this.settingsColor = settingsColor;
    }

    static ThemeStyle defaults() {
        return new ThemeStyle(0xFF060A13, 0, 0xFFFFFFFF, 0xFFFFFFFF,
                0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF);
    }

    static int withAlpha(int color, int alpha) {
        return (color & 0x00FFFFFF) | (Math.max(0, Math.min(255, alpha)) << 24);
    }

    int gradientBottomColor() {
        return withAlpha(gradientColor, Math.round(gradientOpacity * 255f / 100f));
    }
}
