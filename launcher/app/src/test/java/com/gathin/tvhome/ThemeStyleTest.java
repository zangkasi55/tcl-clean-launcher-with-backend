package com.gathin.tvhome;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class ThemeStyleTest {
    @Test public void defaultsLeaveThePhotoAndHeaderColorsUnchanged() {
        ThemeStyle style = ThemeStyle.defaults();
        assertEquals(0, style.gradientOpacity);
        assertEquals(0, style.gradientBottomColor() >>> 24);
        assertEquals(0xFFFFFFFF, style.clockColor);
        assertEquals(0xFFFFFFFF, style.dateColor);
        assertEquals(0xFFFFFFFF, style.inputColor);
        assertEquals(0xFFFFFFFF, style.wifiColor);
        assertEquals(0xFFFFFFFF, style.settingsColor);
    }

    @Test public void gradientOpacityChangesAlphaWithoutChangingItsColor() {
        ThemeStyle style = new ThemeStyle(0xFF123456, 50, 1, 2, 3, 4, 5);
        assertEquals(0x80123456, style.gradientBottomColor());
        assertEquals(1, style.clockColor);
        assertEquals(2, style.dateColor);
        assertEquals(3, style.inputColor);
        assertEquals(4, style.wifiColor);
        assertEquals(5, style.settingsColor);
        assertEquals(0xFF123456, new ThemeStyle(0xFF123456, 100, 1, 2, 3, 4, 5).gradientBottomColor());
        assertEquals(0x00123456, new ThemeStyle(0xFF123456, -1, 1, 2, 3, 4, 5).gradientBottomColor());
    }

    @Test public void dateAndIconTintKeepTheirExpectedAlphaAndRgb() {
        assertEquals(0xA6123456, ThemeStyle.withAlpha(0xFF123456, 166));
        assertEquals(0xFF123456, ThemeStyle.withAlpha(0x00123456, 999));
        assertEquals(0x00123456, ThemeStyle.withAlpha(0xFF123456, -1));
    }
}
