package com.gathin.tvhome;

import java.util.List;

final class AppCatalog {
    private AppCatalog() {}

    static String canonicalComponent(String value) {
        if (value == null || !value.matches("[A-Za-z_][A-Za-z0-9_.]*/[A-Za-z_.$][A-Za-z0-9_.$]*")) {
            throw new IllegalArgumentException("Invalid activity component");
        }
        String[] parts = value.split("/", 2);
        return parts[0] + "/" + (parts[1].startsWith(".") ? parts[0] + parts[1] : parts[1]);
    }

    static int rank(List<String> order, String component) {
        int index = order.indexOf(canonicalComponent(component));
        return index < 0 ? Integer.MAX_VALUE : index;
    }

    static boolean safeAsset(String filename) {
        return filename != null
                && filename.matches("(wallpaper|artwork)-[a-f0-9]{64}\\.(jpg|png)");
    }

    static boolean usableBanner(int width, int height) {
        return width > 0 && height > 0;
    }
}
