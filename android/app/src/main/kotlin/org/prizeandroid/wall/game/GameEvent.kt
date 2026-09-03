package org.prizeandroid.wall.game

/** Side effects the engine wants performed, collected during a `tick()` and
 * carried out by the audio layer (Phase F). Kept separate from the state
 * machine so the engine itself has no Android audio dependency. */
sealed class GameEvent {
    /** A short, fixed sound effect (tick/bell/start) — always safe for a
     * low-latency pool player, never a spoken line. */
    data class Play(val cue: Cue) : GameEvent()

    /** A welcome greeting or a prize's reveal line: unpredictable length (a
     * couple of seconds to Zingbot's ~10s joke), so it needs a player built
     * for arbitrary-length clips rather than one built for sound effects. */
    data class PlayAnnouncement(val cue: Cue) : GameEvent()

    /** The announcer's clip queued straight into the prize's clip, heard as one sentence
     * (ports `channel.queue(cue)` in `begin_reveal`). Always an announcement pair. */
    data class PlayQueued(val first: Cue, val second: Cue) : GameEvent()

    data object StartLoop : GameEvent()
    data object StopLoop : GameEvent()
    data object StartIdleMusic : GameEvent()
    data object FadeIdleMusic : GameEvent()
}
