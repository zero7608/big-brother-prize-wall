package org.prizeandroid.wall.config

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Mirrors RFIDPrize/config.json field-for-field so an existing config authored
 * for the Pi build can be dropped in unchanged. Underscore-prefixed keys in
 * the source file ("_comment", "_prize_order", ...) are plain JSON comments
 * and are simply not modelled here; unknown-key decoding is left lenient.
 */
@Serializable
data class WallConfig(
    val display: DisplayConfig = DisplayConfig(),
    val theme: ThemeConfig = ThemeConfig(),
    val spin: SpinConfig = SpinConfig(),
    val behaviour: BehaviourConfig = BehaviourConfig(),
    val prizes: List<PrizeConfig> = emptyList(),
    @SerialName("key_names") val keyNames: Map<String, String> = emptyMap(),
    val button: ButtonConfig = ButtonConfig(),
    @SerialName("rigged_tags") val riggedTags: Map<String, String> = emptyMap(),
    val reader: ReaderConfig = ReaderConfig(),
)

@Serializable
data class DisplayConfig(
    val fullscreen: Boolean = true,
    val width: Int = 1280,
    val height: Int = 720,
    val title: String = "",
    @SerialName("hide_mouse") val hideMouse: Boolean = true,
    val background: String = "#04101c",
    val accent: String = "#2fd0ff",
    val font: String? = null,
)

@Serializable
data class ThemeConfig(
    @SerialName("house_name") val houseName: String = "",
    @SerialName("idle_line") val idleLine: String = "",
    @SerialName("spin_line") val spinLine: String = "",
    @SerialName("reveal_line") val revealLine: String = "",
    @SerialName("exhausted_line") val exhaustedLine: String = "",
    @SerialName("welcome_line") val welcomeLine: String = "WELCOME, {name}",
    @SerialName("button_line") val buttonLine: String = "",
    @SerialName("default_guest") val defaultGuest: String = "GUEST",
    @SerialName("show_eye") val showEye: Boolean = true,
    @SerialName("shuffle_wall") val shuffleWall: Boolean = false,
    @SerialName("frame_colour") val frameColour: String = "#246f80",
    @SerialName("background_style") val backgroundStyle: String = "timetrip",
    @SerialName("warp_streaks") val warpStreaks: Int = 20,
    @SerialName("warp_seam") val warpSeam: Boolean = true,
    val waves: Boolean = true,
    @SerialName("wave_speed") val waveSpeed: Double = 1.0,
    @SerialName("wave_detail") val waveDetail: Int = 5,
    @SerialName("wave_quality") val waveQuality: Int = 1,
    @SerialName("wave_chroma") val waveChroma: Boolean = true,
    val captions: Boolean = true,
)

@Serializable
data class SpinConfig(
    val seconds: Double = 10.0,
    @SerialName("min_ticks") val minTicks: Int = 72,
    @SerialName("start_sound") val startSound: String? = null,
    @SerialName("tick_sound") val tickSound: String? = null,
    @SerialName("loop_sound") val loopSound: String? = null,
    @SerialName("armed_sound") val armedSound: String? = null,
    @SerialName("bell_sound") val bellSound: String? = null,
    @SerialName("land_pause_seconds") val landPauseSeconds: Double = 0.5,
    @SerialName("reveal_sound") val revealSound: String? = null,
    @SerialName("reveal_hold_seconds") val revealHoldSeconds: Double = 6.0,
    @SerialName("idle_music") val idleMusic: String? = null,
    @SerialName("reveal_tail_seconds") val revealTailSeconds: Double = 2.0,
)

@Serializable
data class BehaviourConfig(
    @SerialName("once_per_night") val oncePerNight: Boolean = true,
    @SerialName("reserve_rigged_prizes") val reserveRiggedPrizes: Boolean = true,
    @SerialName("rescan_lockout_seconds") val rescanLockoutSeconds: Double = 3.0,
    @SerialName("armed_timeout_seconds") val armedTimeoutSeconds: Double = 45.0,
    @SerialName("state_file") val stateFile: String = "night_state.json",
    @SerialName("log_file") val logFile: String = "scans.log",
)

@Serializable
data class PrizeConfig(
    val id: String,
    val name: String,
    val image: String? = null,
    val sound: String? = null,
    val weight: Double = 1.0,
    /** Which announcer clip family introduces this prize's line: "congrats" for
     * a normal win, "badnews" for Slop, "none" when the prize speaks for itself
     * (Zingbot). Matches Prize.__init__'s `spec.get("opener", "congrats")`. */
    val opener: String = "congrats",
)

@Serializable
data class ButtonConfig(
    val pin: Int = 17,
    @SerialName("pull_up") val pullUp: Boolean = true,
    @SerialName("bounce_seconds") val bounceSeconds: Double = 0.05,
    val type: String = "wireless",
)

/** Only `type: "ble"` is honoured on Android; wired/Classic entries are read but unused. */
@Serializable
data class ReaderConfig(
    val type: String = "ble",
    val port: String? = null,
    val baud: Int = 115200,
    @SerialName("reconnect_seconds") val reconnectSeconds: Double = 3.0,
    @SerialName("cs_pin") val csPin: String? = null,
    @SerialName("poll_interval") val pollInterval: Double = 0.15,
    @SerialName("read_timeout") val readTimeout: Double = 0.5,
    val name: String = "PrizeShack",
    val address: String = "",
)
