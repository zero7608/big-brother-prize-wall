package org.prizeandroid.wall.game

/** A loaded, ready-to-play sound cue. Duration is known up front, same as
 * pygame's `Sound.get_length()` on an already-decoded clip, so the engine can
 * compute how long to hold a state without ever touching a media API itself. */
data class Cue(val assetPath: String, val durationSeconds: Double)

/**
 * Everything the game engine needs to know about sound, without depending on
 * any Android audio API — the real implementation (Phase F, `AudioController`)
 * loads assets/sounds, and tests can substitute a fake with fixed durations.
 *
 * Mirrors prize_wall.py's cue lookups: `scfg.get(...)`, `Prize._load_sound`,
 * and `PrizeWall.opener_sound`.
 */
interface CueLibrary {
    val startCue: Cue?
    val loopCue: Cue?
    val tickCue: Cue?
    val bellCue: Cue?
    val armedCue: Cue?
    val revealCue: Cue?
    val idleMusic: Cue?

    fun prizeSound(prize: Prize): Cue?

    /**
     * The announcer's half of a line for this family/guest, e.g. `opener_sound(
     * "welcome", "Alex")`. Picks one of several takes at random when more than
     * one exists (see SOURCES.md), falls back to the unnamed clip of the same
     * family, and returns null if neither exists.
     */
    fun openerSound(family: String, guestName: String): Cue?
}

/** `Alex` -> `autumn`; ports `PrizeWall.name_slug`. */
fun nameSlug(name: String?): String {
    val slug = (name ?: "").lowercase().replace(Regex("[^a-z0-9]+"), "")
    return slug.ifEmpty { "guest" }
}

/** No sound at all — used until Phase F wires up real audio, and in tests. */
object SilentCueLibrary : CueLibrary {
    override val startCue: Cue? = null
    override val loopCue: Cue? = null
    override val tickCue: Cue? = null
    override val bellCue: Cue? = null
    override val armedCue: Cue? = null
    override val revealCue: Cue? = null
    override val idleMusic: Cue? = null
    override fun prizeSound(prize: Prize): Cue? = null
    override fun openerSound(family: String, guestName: String): Cue? = null
}
