package org.prizeandroid.wall.game

/** What the engine needs from the reader/button, decoupled from BLE.
 * `WirelessLink` (Phase C) implements the shape of this directly. */
interface InputSource {
    /** One queued tag UID, or null if none is waiting. Called up to 8x per tick,
     * same bound as `poll_keys`, so a wedged queue can't stall the loop. */
    fun readTag(): String?

    /** True at most once per press; ports `button.pressed()`. */
    fun buttonPressed(): Boolean

    fun clearButton()
}
