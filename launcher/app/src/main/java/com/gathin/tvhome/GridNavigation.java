package com.gathin.tvhome;

/** Physical left/right boundaries for a grid whose first column follows its reading direction. */
final class GridNavigation {
    private GridNavigation() { }

    static int visibleTileCount(int total, int columns, boolean allApps) {
        return allApps ? total : Math.min(columns, total);
    }

    static int panelPosition(int total, int columns, boolean energy, boolean allApps) {
        return energy && !allApps ? visibleTileCount(total, columns, false) : -1;
    }

    static int tileIndex(int position, int panelPosition) {
        if (panelPosition >= 0 && position == panelPosition) return -1;
        return panelPosition >= 0 && position > panelPosition ? position - 1 : position;
    }

    static int adapterPosition(int tileIndex, int panelPosition) {
        return panelPosition >= 0 && tileIndex >= panelPosition ? tileIndex + 1 : tileIndex;
    }

    static boolean atHorizontalEdge(int position, int count, int columns, boolean rtl, boolean left) {
        boolean towardStart = left != rtl;
        if (towardStart) return position % columns == 0;
        return position % columns == columns - 1 || position == count - 1;
    }
}
