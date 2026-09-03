package org.prizeandroid.wall.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.ColorMatrix
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin
import org.prizeandroid.wall.config.WallConfig
import org.prizeandroid.wall.game.EngineSnapshot
import org.prizeandroid.wall.game.TileRenderState
import org.prizeandroid.wall.game.WallState

private fun easeInOutSine(p: Float): Float = -(kotlin_cos(Math.PI.toFloat() * p) - 1f) / 2f
private fun kotlin_cos(x: Float): Float = kotlin.math.cos(x)
private fun clamp01(v: Float): Float = v.coerceIn(0f, 1f)

/**
 * The Canvas-based wall: background, framed tiles, header/footer copy and the
 * armed/reveal overlays. Ports the drawing intent of `visuals.py` and
 * `prize_wall.py`'s `draw_*` methods; the elaborate pre-rendered "timetrip"
 * background is approximated here with a live-drawn gradient, grid and
 * pulsing alcoves rather than pygame's cached-surface approach.
 */
@Composable
fun PrizeWallScreen(config: WallConfig, snapshot: EngineSnapshot, modifier: Modifier = Modifier) {
    var animNow by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(Unit) {
        var start = 0L
        var first = true
        while (true) {
            withFrameNanos { t ->
                if (first) { start = t; first = false }
                animNow = (t - start) / 1_000_000_000f
            }
        }
    }

    val context = LocalContext.current
    val bg = hexColor(config.display.background, Color(0xFF04101C))
    val accent = hexColor(config.display.accent, Color(0xFF2FD0FF))
    val frameColour = hexColor(config.theme.frameColour, Color(0xFF246F80))
    val imageCache = remember(context) { AssetImageCache(context) }
    val wallFont = remember(context, config.display.font) {
        val path = config.display.font?.let { "fonts/$it" } ?: return@remember null
        runCatching { FontFamily(Font(path, context.assets)) }.getOrNull()
    }

    BoxWithConstraints(modifier = modifier.fillMaxSize().background(bg)) {
        val layout = remember(maxWidth, maxHeight, snapshot.tiles.size) {
            computeWallLayout(maxWidth, maxHeight, snapshot.tiles.size.coerceAtLeast(1))
        }

        WallBackground(bg, accent, layout, snapshot.tiles.size, animNow)

        for (tile in snapshot.tiles) {
            val rect = layout.tiles.getOrNull(tile.slot) ?: continue
            PrizeTile(
                tile = tile,
                rect = rect,
                accent = accent,
                frameColour = frameColour,
                captions = config.theme.captions,
                imageCache = imageCache,
                fontFamily = wallFont,
            )
        }

        HeaderBar(config.theme.houseName, accent, layout.headerHeight, wallFont)
        FooterLine(config, snapshot, accent, animNow, wallFont)

        if (snapshot.state == WallState.ARMED) {
            ArmedOverlay(config, snapshot, accent, animNow)
        }
        if (snapshot.state == WallState.REVEAL) {
            RevealOverlay(config, snapshot, accent, animNow, imageCache)
        }
    }
}

/** Cyan/purple/magenta, the same trio `visuals.py`'s TimeTripField pulses the alcoves through. */
private val alcoveHues = listOf(Color(0xFF2FD0FF), Color(0xFFB06CFF), Color(0xFFFF5FD0))

/** One drifting neon streak. `cross` is its fixed position on the axis it
 * does NOT move along (a row for a horizontal streak, a column for a vertical
 * one) as a 0..1 fraction of that dimension. */
private data class Streak(val cross: Float, val speed: Float, val length: Float, val phase: Float, val colour: Color)

private fun makeStreaks(count: Int, seedOffset: Int): List<Streak> = List(count) { i ->
    val seed = (i + seedOffset) * 37
    Streak(
        cross = (seed % 97) / 97f,
        speed = 0.08f + (seed % 53) / 53f * 0.14f,
        length = 60f + (seed % 40) * 3f,
        phase = (seed % 31) / 31f,
        colour = alcoveHues[i % alcoveHues.size],
    )
}

@Composable
private fun WallBackground(
    bg: Color,
    accent: Color,
    layout: WallLayout,
    tileCount: Int,
    animNow: Float,
) {
    val streaks = remember(tileCount) { makeStreaks(20, seedOffset = 0) }
    val verticalStreaks = remember(tileCount) { makeStreaks(14, seedOffset = 200) }

    Canvas(modifier = Modifier.fillMaxSize()) {
        // Deep-space vertical gradient rather than a flat fill, darker at the
        // top and bottom, lightest at mid-height — the "navy under a grid" look.
        val top = bg
        val mid = Color(
            (bg.red + accent.red * 0.10f).coerceAtMost(1f),
            (bg.green + accent.green * 0.10f).coerceAtMost(1f),
            (bg.blue + accent.blue * 0.14f).coerceAtMost(1f),
        )
        drawRect(brush = Brush.verticalGradient(listOf(top, mid, top)), size = size)

        // A digital grid, faint, drifting very slowly.
        val gridSpacing = 48.dp.toPx()
        val gridColor = accent.copy(alpha = 0.06f)
        val offsetX = (animNow * 4f) % gridSpacing
        val offsetY = (animNow * 6f) % gridSpacing
        var x = -offsetX
        while (x < size.width) {
            drawLine(gridColor, Offset(x, 0f), Offset(x, size.height), strokeWidth = 1f)
            x += gridSpacing
        }
        var y = -offsetY
        while (y < size.height) {
            drawLine(gridColor, Offset(0f, y), Offset(size.width, y), strokeWidth = 1f)
            y += gridSpacing
        }

        // A glowing alcove behind each frame, shaped like the frame itself
        // (not a plain circle) with a soft radial falloff, cycling through
        // the same cyan/purple/magenta trio and pulsing out of phase.
        layout.tiles.forEachIndexed { index, tile ->
            val phase = index * 1.9f
            val pulse = 0.5f + 0.5f * sin(animNow * 0.5f + phase)
            val hue = alcoveHues[index % alcoveHues.size]
            val cx = tile.x.toPx() + tile.width.toPx() / 2f
            val cy = tile.y.toPx() + tile.height.toPx() / 2f
            val w = tile.width.toPx() * 1.5f
            val h = tile.height.toPx() * 1.5f
            val glowAlpha = 0.10f + 0.10f * pulse
            drawOval(
                brush = Brush.radialGradient(
                    colors = listOf(hue.copy(alpha = glowAlpha), hue.copy(alpha = 0f)),
                    center = Offset(cx, cy),
                    radius = max(w, h) / 2f,
                ),
                topLeft = Offset(cx - w / 2f, cy - h / 2f),
                size = androidx.compose.ui.geometry.Size(w, h),
            )
        }

        // Neon streaks drifting left to right at varying heights and speeds.
        for (streak in streaks) {
            val p = (animNow * streak.speed + streak.phase) % 1f
            val cx = size.width * p
            val cy = size.height * streak.cross
            drawLine(
                brush = Brush.horizontalGradient(
                    listOf(
                        streak.colour.copy(alpha = 0f),
                        streak.colour.copy(alpha = 0.5f),
                        streak.colour.copy(alpha = 0f),
                    ),
                    startX = cx - streak.length,
                    endX = cx + streak.length,
                ),
                start = Offset(cx - streak.length, cy),
                end = Offset(cx + streak.length, cy),
                strokeWidth = 4.dp.toPx(),
            )
        }

        // The same drift, turned 90 degrees: neon streaks scrolling top to bottom.
        for (streak in verticalStreaks) {
            val p = (animNow * streak.speed + streak.phase) % 1f
            val cy = size.height * p
            val cx = size.width * streak.cross
            drawLine(
                brush = Brush.verticalGradient(
                    listOf(
                        streak.colour.copy(alpha = 0f),
                        streak.colour.copy(alpha = 0.5f),
                        streak.colour.copy(alpha = 0f),
                    ),
                    startY = cy - streak.length,
                    endY = cy + streak.length,
                ),
                start = Offset(cx, cy - streak.length),
                end = Offset(cx, cy + streak.length),
                strokeWidth = 4.dp.toPx(),
            )
        }

        // A warp seam: a soft band sweeping left to right every few seconds.
        val seamPeriod = 7f
        val seamP = (animNow % seamPeriod) / seamPeriod
        val seamX = size.width * seamP
        drawRect(
            brush = Brush.horizontalGradient(
                listOf(accent.copy(alpha = 0f), accent.copy(alpha = 0.10f), accent.copy(alpha = 0f)),
                startX = seamX - 60.dp.toPx(),
                endX = seamX + 60.dp.toPx(),
            ),
            topLeft = Offset(seamX - 60.dp.toPx(), 0f),
            size = androidx.compose.ui.geometry.Size(120.dp.toPx(), size.height),
        )
    }
}

@Composable
private fun HeaderBar(
    houseName: String,
    accent: Color,
    headerHeight: androidx.compose.ui.unit.Dp,
    fontFamily: FontFamily?,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(top = headerHeight * 0.32f),
        contentAlignment = Alignment.TopCenter,
    ) {
        Text(
            text = houseName.uppercase(),
            color = accent,
            fontSize = (headerHeight.value * 0.42f).sp,
            fontFamily = fontFamily,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun FooterLine(
    config: WallConfig,
    snapshot: EngineSnapshot,
    accent: Color,
    animNow: Float,
    fontFamily: FontFamily?,
) {
    if (snapshot.state == WallState.ARMED) return // the armed panel carries its own copy

    val theme = config.theme
    val (text, colour) = when (snapshot.state) {
        WallState.IDLE -> theme.idleLine to accent
        WallState.SPINNING -> {
            val dots = ".".repeat((animNow * 3).toInt() % 4)
            (theme.spinLine + dots) to accent
        }
        WallState.LANDED, WallState.REVEAL -> theme.revealLine to accent
        WallState.EXHAUSTED -> (theme.exhaustedLine + "  -  RESET TO PLAY AGAIN") to Color(0xFFDC5A5A)
        WallState.ARMED -> "" to accent
    }

    val pulseColour = if (snapshot.state == WallState.IDLE) {
        val k = 0.45f + 0.55f * easeInOutSine(clamp01((sin(animNow * 1.9f) + 1f) / 2f))
        Color(colour.red * k, colour.green * k, colour.blue * k, colour.alpha)
    } else colour

    Box(modifier = Modifier.fillMaxSize().padding(bottom = 18.dp), contentAlignment = Alignment.BottomCenter) {
        Text(
            text = text.uppercase(),
            color = pulseColour,
            fontSize = 20.sp,
            fontFamily = fontFamily,
            fontWeight = FontWeight.Bold,
        )
    }
}

/** Full desaturation, ports `greyscale()`'s evicted-houseguest look. */
private val greyscaleFilter = ColorFilter.colorMatrix(ColorMatrix().apply { setToSaturation(0f) })

@Composable
private fun PrizeTile(
    tile: TileRenderState,
    rect: TileRect,
    accent: Color,
    frameColour: Color,
    captions: Boolean,
    imageCache: AssetImageCache,
    fontFamily: FontFamily?,
) {
    val litColour = if (tile.lit) accent else frameColour
    val borderWidth = if (tile.lit) 4.dp else 2.dp
    val bitmap = imageCache.get(tile.image)

    Box(
        modifier = Modifier
            .offset(x = rect.x, y = rect.y)
            .size(rect.width, rect.height),
    ) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = tile.name,
                contentScale = ContentScale.Crop,
                colorFilter = if (tile.evicted) greyscaleFilter else null,
                alpha = if (tile.evicted) 0.55f else 1f,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            // No artwork configured yet: the same plain coloured box with a
            // caption prize_wall.py falls back to until a photo is dropped in.
            val fillColour = tilePalette(tile.prizeId)
            val rendered = if (tile.evicted) desaturate(fillColour) else fillColour
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(rendered.copy(alpha = if (tile.evicted) 0.35f else 0.85f)),
            )
        }
        Canvas(modifier = Modifier.fillMaxSize()) {
            drawRect(color = litColour, style = Stroke(width = borderWidth.toPx()))
            if (tile.evicted) {
                drawLine(Color(0xFFDC5A5A), Offset(0f, 0f), Offset(size.width, size.height), strokeWidth = 3.dp.toPx())
                drawLine(Color(0xFFDC5A5A), Offset(size.width, 0f), Offset(0f, size.height), strokeWidth = 3.dp.toPx())
            }
        }
        if (captions) {
            Text(
                text = tile.name.uppercase(),
                color = if (tile.evicted) Color(0xFF8A97A6) else Color.White,
                fontSize = 12.sp,
                fontFamily = fontFamily,
                textAlign = TextAlign.Center,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 4.dp),
            )
        }
    }
}

@Composable
private fun ArmedOverlay(config: WallConfig, snapshot: EngineSnapshot, accent: Color, animNow: Float) {
    val welcome = config.theme.welcomeLine.replace("{name}", snapshot.armedName.uppercase())
    val speaking = max(0.0, snapshot.speakingUntil - snapshot.now)
    val ready = clamp01(1f - (speaking / 0.45).toFloat())
    val pulse = 0.55f + 0.45f * easeInOutSine(clamp01((sin(animNow * 2.6f) + 1f) / 2f))
    val promptAlpha = pulse * ready

    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Box(
            modifier = Modifier
                .background(Color(0xFF030C16).copy(alpha = 0.91f))
                .padding(horizontal = 32.dp, vertical = 20.dp),
        ) {
            Box(
                modifier = Modifier.padding(4.dp),
            ) {
                androidx.compose.foundation.layout.Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text(
                        text = welcome.uppercase(),
                        color = accent,
                        fontSize = 26.sp,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                    )
                    Text(
                        text = config.theme.buttonLine.uppercase(),
                        color = Color.White.copy(alpha = promptAlpha),
                        fontSize = 20.sp,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun RevealOverlay(
    config: WallConfig,
    snapshot: EngineSnapshot,
    accent: Color,
    animNow: Float,
    imageCache: AssetImageCache,
) {
    val elapsed = clamp01((snapshot.now - snapshot.stateSince).toFloat() / 0.5f)
    val p = 1f - (1f - elapsed).pow(4)
    val bitmap = imageCache.get(snapshot.winnerImage)
    // 4:3, matching the frames on the wall, zooming up from the tile size to a hero shot.
    val cardWidth = (260 + 220 * p).dp
    val cardHeight = cardWidth * 3f / 4f

    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        // A dark veil behind the hero card so it reads clearly over the busy background.
        Box(
            modifier = Modifier
                .size(cardWidth + 48.dp, cardHeight + 96.dp)
                .background(Color(0xFF030C16).copy(alpha = 0.35f + 0.5f * p)),
        )
        androidx.compose.foundation.layout.Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(
                modifier = Modifier
                    .size(cardWidth, cardHeight)
                    .background(Color(0xFF030C16)),
            ) {
                if (bitmap != null) {
                    Image(
                        bitmap = bitmap,
                        contentDescription = snapshot.winnerName,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .background(tilePalette(snapshot.winnerName ?: "?").copy(alpha = 0.85f)),
                    )
                }
                Canvas(modifier = Modifier.fillMaxSize()) {
                    drawRect(color = accent, style = Stroke(width = (3 + 2 * p).dp.toPx()))
                }
            }
            Text(
                text = (snapshot.winnerName ?: "").uppercase(),
                color = accent,
                fontSize = (24 + 10 * p).sp,
                fontWeight = FontWeight.Bold,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 16.dp),
            )
        }
    }
}

/** Stable placeholder colour per prize until Phase G supplies real photos —
 * the same "plain coloured box with its name under it" prize_wall.py falls
 * back to when a prize has no `image` configured. */
private val palette = listOf(
    Color(0xFFC63D3D), Color(0xFF3D80C6), Color(0xFF4CA860), Color(0xFFC48C34),
    Color(0xFF8C54BE), Color(0xFF34A8A8), Color(0xFFC66096), Color(0xFF6E7A9E),
    Color(0xFFA87848),
)

private fun tilePalette(prizeId: String): Color =
    palette[(prizeId.hashCode().mod(palette.size))]

private fun desaturate(color: Color): Color {
    val grey = (color.red * 0.3f + color.green * 0.59f + color.blue * 0.11f)
    return Color(grey, grey, grey, color.alpha)
}
