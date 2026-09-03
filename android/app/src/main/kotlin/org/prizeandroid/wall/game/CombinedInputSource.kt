package org.prizeandroid.wall.game

import org.prizeandroid.wall.ble.WirelessLink

/**
 * Feeds the engine from the real wireless unit, with a manual override so the
 * wall can be tested without tapping a physical tag — the on-device analogue
 * of prize_wall.py's `--simulate`/`--keys` keyboard stand-ins.
 */
class CombinedInputSource(private val link: WirelessLink) : InputSource {
    private val simulatedTags = ArrayDeque<String>()
    private var simulatedButton = false
    private val lock = Any()

    fun simulateTag(uid: String) = synchronized(lock) { simulatedTags.addLast(uid) }

    fun simulateButtonPress() = synchronized(lock) { simulatedButton = true }

    override fun readTag(): String? {
        link.readTag()?.let { return it }
        return synchronized(lock) { if (simulatedTags.isEmpty()) null else simulatedTags.removeFirst() }
    }

    override fun buttonPressed(): Boolean {
        val real = link.takeButton()
        val simulated = synchronized(lock) {
            val was = simulatedButton
            simulatedButton = false
            was
        }
        return real || simulated
    }

    override fun clearButton() {
        link.clearButton()
        synchronized(lock) { simulatedButton = false }
    }
}
