package com.gathin.tvhome;

import org.junit.Test;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertEquals;

public class GridNavigationTest {
    @Test public void homeContainsOnlyOneDockRowAndAllAppsKeepsTheFullCatalog() {
        assertEquals(5, GridNavigation.visibleTileCount(39, 5, false));
        assertEquals(39, GridNavigation.visibleTileCount(39, 5, true));
        assertEquals(3, GridNavigation.visibleTileCount(3, 5, false));
        assertEquals(3, GridNavigation.visibleTileCount(3, 5, true));
        assertEquals(0, GridNavigation.visibleTileCount(0, 5, false));
    }

    @Test public void energyOnlyAppearsOnHomeWithoutTakingAnyAppsOffTheNextPage() {
        assertEquals(5, GridNavigation.panelPosition(39, 5, true, false));
        assertEquals(3, GridNavigation.panelPosition(3, 5, true, false));
        assertEquals(-1, GridNavigation.panelPosition(39, 5, true, true));
        assertEquals(-1, GridNavigation.panelPosition(39, 5, false, false));
        assertEquals(-1, GridNavigation.panelPosition(39, 5, false, true));
    }

    @Test public void informationRowDoesNotConsumeAnAppOrDpadColumn() {
        assertEquals(-1, GridNavigation.tileIndex(5, 5));
        for (int index = 0; index < 39; index++) {
            int position = GridNavigation.adapterPosition(index, 5);
            assertEquals(index, GridNavigation.tileIndex(position, 5));
            assertEquals(index < 5 ? index : index + 1, position);
        }
        int belowPanel = GridNavigation.tileIndex(6, 5);
        assertTrue(GridNavigation.atHorizontalEdge(belowPanel, 39, 5, false, true));
        assertEquals(0, GridNavigation.adapterPosition(belowPanel - 5, 5));
    }

    @Test public void disabledPanelAndShortCatalogKeepTheirAppPositions() {
        for (int index = 0; index < 39; index++) {
            assertEquals(index, GridNavigation.adapterPosition(index, -1));
            assertEquals(index, GridNavigation.tileIndex(index, -1));
        }
        assertEquals(2, GridNavigation.adapterPosition(2, 3));
        assertEquals(-1, GridNavigation.tileIndex(3, 3));
        assertEquals(-1, GridNavigation.tileIndex(0, 0));
    }

    @Test public void hebrewDockStartsOnTheRightAndStopsAtTheLeft() {
        assertTrue(GridNavigation.atHorizontalEdge(0, 11, 4, true, false));
        assertFalse(GridNavigation.atHorizontalEdge(0, 11, 4, true, true));
        assertTrue(GridNavigation.atHorizontalEdge(3, 11, 4, true, true));
        assertFalse(GridNavigation.atHorizontalEdge(3, 11, 4, true, false));
    }

    @Test public void incompleteHebrewLastRowDoesNotWrapIntoEmptySpace() {
        assertTrue(GridNavigation.atHorizontalEdge(8, 11, 4, true, false));
        assertTrue(GridNavigation.atHorizontalEdge(10, 11, 4, true, true));
        assertFalse(GridNavigation.atHorizontalEdge(9, 11, 4, true, true));
        assertFalse(GridNavigation.atHorizontalEdge(9, 11, 4, true, false));
    }

    @Test public void originalLeftToRightBoundariesArePreserved() {
        assertTrue(GridNavigation.atHorizontalEdge(0, 11, 4, false, true));
        assertTrue(GridNavigation.atHorizontalEdge(3, 11, 4, false, false));
        assertTrue(GridNavigation.atHorizontalEdge(10, 11, 4, false, false));
        assertFalse(GridNavigation.atHorizontalEdge(1, 11, 4, false, true));
        assertFalse(GridNavigation.atHorizontalEdge(1, 11, 4, false, false));
    }

    @Test public void singleColumnHasBothHorizontalEdges() {
        assertTrue(GridNavigation.atHorizontalEdge(1, 3, 1, true, true));
        assertTrue(GridNavigation.atHorizontalEdge(1, 3, 1, true, false));
        assertTrue(GridNavigation.atHorizontalEdge(1, 3, 1, false, true));
        assertTrue(GridNavigation.atHorizontalEdge(1, 3, 1, false, false));
    }
}
