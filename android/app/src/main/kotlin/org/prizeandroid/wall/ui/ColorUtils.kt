package org.prizeandroid.wall.ui

import androidx.compose.ui.graphics.Color

/** Ports `hex_colour`: "#RRGGBB" -> a Color, falling back if blank/malformed. */
fun hexColor(value: String?, fallback: Color): Color {
    val hex = value?.trim()?.removePrefix("#")
    if (hex.isNullOrEmpty() || hex.length < 6) return fallback
    return runCatching {
        val r = hex.substring(0, 2).toInt(16)
        val g = hex.substring(2, 4).toInt(16)
        val b = hex.substring(4, 6).toInt(16)
        Color(r, g, b)
    }.getOrDefault(fallback)
}
