package org.prizeandroid.wall.ui

import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlin.math.ceil
import kotlin.math.sqrt

/** One frame's position and size, in Dp, within the wall canvas. */
data class TileRect(val x: Dp, val y: Dp, val width: Dp, val height: Dp)

data class WallLayout(
    val headerHeight: Dp,
    val footerHeight: Dp,
    val tiles: List<TileRect>, // index = slot, row-major
)

/**
 * Ports the geometry from `PrizeWall.layout()`/`tile_rect`: a header bar, a
 * footer strip, and a grid of 4:3 frames in between sized to the available
 * height and centred with the leftover width as the gap between them.
 */
fun computeWallLayout(width: Dp, height: Dp, count: Int): WallLayout {
    val headerHeight = height * 0.135f
    val footerHeight = height * 0.10f
    val gridTop = headerHeight
    val gridHeight = height - headerHeight - footerHeight
    val marginX = width * 0.04f
    val gridWidth = width - marginX * 2

    val cols = ceil(sqrt(count.toDouble())).toInt().coerceAtLeast(1)
    val rows = ceil(count.toDouble() / cols).toInt().coerceAtLeast(1)

    val rowGap = gridHeight * 0.06f / rows
    val cellHeight = (gridHeight - rowGap * (rows - 1)) / rows
    val cellWidthFromAspect = cellHeight * 4f / 3f
    val colGapFraction = 0.06f
    val naiveCellWidth = gridWidth / (cols + (cols - 1) * colGapFraction)
    val cellWidth = if (cellWidthFromAspect < naiveCellWidth) cellWidthFromAspect else naiveCellWidth
    val colGap = if (cols > 1) (gridWidth - cellWidth * cols) / (cols - 1) else 0.dp

    val tiles = (0 until count).map { index ->
        val row = index / cols
        val col = index % cols
        val rowsInThisRow = minOf(cols, count - row * cols)
        val rowWidth = cellWidth * rowsInThisRow + colGap * (rowsInThisRow - 1)
        val rowStartX = marginX + (gridWidth - rowWidth) / 2f
        TileRect(
            x = rowStartX + (cellWidth + colGap) * col,
            y = gridTop + (cellHeight + rowGap) * row,
            width = cellWidth,
            height = cellHeight,
        )
    }
    return WallLayout(headerHeight, footerHeight, tiles)
}
