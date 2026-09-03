package org.prizeandroid.wall.game

import android.util.Log
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.min
import kotlin.math.sqrt
import kotlin.random.Random
import org.prizeandroid.wall.config.WallConfig

private const val TAG = "PrizeWallEngine"

/**
 * Kotlin port of `PrizeWall`'s game logic from prize_wall.py, with the
 * pygame/rendering/audio-playback concerns stripped out: this class only
 * owns the state machine, the weighted draw, the shuffle route, and night
 * persistence. Call `tick()` once per frame the way `PrizeWall.run()` calls
 * its per-frame block; the caller (a ViewModel) owns the actual clock,
 * renders from the exposed read-only state, and carries out the returned
 * `GameEvent`s (Phase F).
 */
class PrizeWallEngine(
    private val config: WallConfig,
    private val cues: CueLibrary,
    private val stateStore: NightStateStore,
    private val logger: ScanLogger,
    private val random: Random = Random.Default,
) {
    val prizes: List<Prize> = config.prizes.mapIndexed { i, spec -> Prize.from(i, spec) }
    private val byId: Map<String, Prize> = prizes.associateBy { it.id }

    init {
        for ((uid, pid) in config.riggedTags) {
            require(pid in byId) { "rigged_tags: $uid points at unknown prize id '$pid'" }
        }
    }

    private val onceFerNight = config.behaviour.oncePerNight
    private val reserveRigged = config.behaviour.reserveRiggedPrizes
    private val lockoutSeconds = config.behaviour.rescanLockoutSeconds
    private val armedTimeoutSeconds = config.behaviour.armedTimeoutSeconds
    private val spinSeconds = config.spin.seconds
    private val minTicks = config.spin.minTicks
    private val landPauseSeconds = config.spin.landPauseSeconds
    private val revealHoldSeconds = config.spin.revealHoldSeconds
    private val revealTailSeconds = config.spin.revealTailSeconds
    private val shuffleWall = config.theme.shuffleWall

    // -- wall arrangement: position -> index into `prizes` --------------------

    var order: List<Int> = emptyList(); private set
    private var slot: Map<Int, Int> = emptyMap()

    // -- the night's memory -----------------------------------------------

    private val claimed = mutableSetOf<String>()
    private val usedKeys = mutableSetOf<String>()
    private val seenAt = mutableMapOf<String, Double>()

    // -- state machine ------------------------------------------------------

    var state: WallState = WallState.IDLE; private set
    var stateSince: Double = 0.0; private set
    var now: Double = 0.0; private set

    var armedUid: String? = null; private set
    var armedName: String = ""; private set
    var speakingUntil: Double = 0.0; private set

    var winner: Prize? = null; private set
    var spinActive: List<Int> = emptyList(); private set
    private var spinSequence: List<Int> = emptyList()
    private var spinTicks = 0
    private var spinStepShown: Int? = null
    private var revealUntil = 0.0

    private val pendingEvents = mutableListOf<GameEvent>()

    init {
        applyOrder(buildOrder())
        loadState()
        if (cues.idleMusic != null) pendingEvents.add(GameEvent.StartIdleMusic)
        if (openToAll().isEmpty()) state = WallState.EXHAUSTED
        pendingEvents.clear() // init-time events aren't meaningful before a ViewModel is listening
    }

    // ---------------------------------------------------------------- layout

    /** Prizes that only their own key can win. */
    private fun hiddenIndices(): Set<Int> {
        val rigged = config.riggedTags.values.toSet()
        return prizes.filter { it.id in rigged }.map { it.index }.toSet()
    }

    /** True if no two rigged prizes share a row or a column. */
    private fun scattered(order: List<Int>): Boolean {
        val cols = ceil(sqrt(prizes.size.toDouble())).toInt()
        val slots = hiddenIndices().map { order.indexOf(it) }
        val rows = slots.map { it / cols }
        val columns = slots.map { it % cols }
        return rows.toSet().size == rows.size && columns.toSet().size == columns.size
    }

    private fun buildOrder(): List<Int> {
        val identity = prizes.indices.toList()
        if (!shuffleWall) return identity
        repeat(500) {
            val candidate = identity.shuffled(random)
            if (scattered(candidate)) return candidate
        }
        Log.w(TAG, "could not find a scattered arrangement; using the last one")
        return identity
    }

    private fun applyOrder(newOrder: List<Int>) {
        order = newOrder
        slot = newOrder.withIndex().associate { (s, prizeIndex) -> prizeIndex to s }
    }

    fun orderIds(): List<String> = order.map { prizes[it].id }

    /** Position on the wall (row-major slot index) for a prize's fixed index. */
    fun slotOf(prizeIndex: Int): Int = slot.getValue(prizeIndex)

    // ------------------------------------------------------------ persistence

    private fun loadState() {
        val saved = stateStore.load() ?: return
        if (onceFerNight) {
            claimed.clear()
            claimed.addAll(saved.claimed.filter { it in byId })
            usedKeys.clear()
            usedKeys.addAll(saved.usedKeys)
        }
        val ids = saved.wallOrder
        if (shuffleWall && ids.isNotEmpty() && ids.sorted() == prizes.map { it.id }.sorted()) {
            val indexById = prizes.associate { it.id to it.index }
            applyOrder(ids.map { indexById.getValue(it) })
        } else if (ids.isNotEmpty() && shuffleWall) {
            Log.w(TAG, "saved wall arrangement no longer matches the prizes; reshuffling")
        }
        if (claimed.isNotEmpty()) {
            Log.i(TAG, "Resuming night: ${claimed.size} prize(s) already won (${claimed.sorted().joinToString(", ")})")
        }
    }

    private fun saveState() {
        stateStore.save(NightStateData(claimed.sorted(), usedKeys.sorted(), orderIds()))
    }

    fun resetNight() {
        claimed.clear()
        usedKeys.clear()
        stateStore.clear()
        applyOrder(buildOrder())
        winner = null
        state = WallState.IDLE
        stateSince = now
        Log.i(TAG, "Night reset: every prize is back on the wall.")
    }

    // --------------------------------------------------------------- queries

    /** Won and finished with, so its photo should render black and white.
     * The prize currently being played for is not evicted yet, even though it
     * is already claimed, so it stays in colour through its own reveal. */
    fun isEvicted(prize: Prize): Boolean = prize.id in claimed && prize !== winner

    fun available(): List<Prize> = prizes.filter { it.id !in claimed }

    /** Rigged prizes being held back for a key that has not been tapped yet. */
    private fun reservedIds(): Set<String> {
        if (!reserveRigged) return emptySet()
        return config.riggedTags.filterKeys { it !in usedKeys }.values.toSet()
    }

    /** What an ordinary, unknown key could still win. */
    fun openToAll(): List<Prize> {
        val reserved = reservedIds()
        return available().filter { it.id !in reserved }
    }

    // ------------------------------------------------------------- selection

    private fun pickRandomPrize(): Prize? {
        val unreserved = openToAll()
        var pool = unreserved.filter { it.weight > 0 }
        if (pool.isEmpty()) pool = unreserved
        if (pool.isEmpty()) return null
        val total = pool.sumOf { it.weight }.let { if (it > 0) it else pool.size.toDouble() }
        val roll = random.nextDouble() * total
        var upto = 0.0
        for (prize in pool) {
            upto += if (prize.weight > 0) prize.weight else 1.0
            if (roll <= upto) return prize
        }
        return pool.last()
    }

    fun keyName(uid: String): String =
        config.keyNames[uid] ?: config.theme.defaultGuest

    // ----------------------------------------------------------- the tick

    /** Runs one frame's worth of game logic; returns cues to play this frame. */
    fun tick(nowSeconds: Double, input: InputSource): List<GameEvent> {
        now = nowSeconds
        pendingEvents.clear()

        val fresh = pollKeys(input)

        when (state) {
            WallState.IDLE, WallState.ARMED, WallState.EXHAUSTED -> {
                if (fresh.isNotEmpty()) arm(fresh[0], input)

                if (state == WallState.ARMED) {
                    if (now < speakingUntil) {
                        // let the greeting finish
                    } else if (input.buttonPressed()) {
                        beginSpin(armedUid!!)
                    } else if (now - stateSince >= armedTimeoutSeconds) {
                        Log.i(TAG, "$armedUid: no button press, back to idle")
                        backToIdle()
                    }
                } else {
                    input.buttonPressed() // drain, so a stray press cannot queue
                }
            }
            WallState.SPINNING -> {
                if (now - stateSince >= spinSeconds) finishSpin() else spinAudio()
            }
            WallState.LANDED -> {
                if (now - stateSince >= landPauseSeconds) beginReveal()
            }
            WallState.REVEAL -> {
                if (now >= revealUntil) backToIdle()
            }
        }

        return pendingEvents.toList()
    }

    /** Drains up to 8 fresh tag reads, applying the same off-the-reader lockout
     * as `poll_keys`: a key resting on the pad is seen every call and never
     * counts as a new tap until it has been away for `rescan_lockout_seconds`. */
    private fun pollKeys(input: InputSource): List<String> {
        val fresh = mutableListOf<String>()
        repeat(8) {
            val uid = input.readTag() ?: return@repeat
            if (now - seenAt.getOrDefault(uid, -1e9) >= lockoutSeconds) fresh.add(uid)
            seenAt[uid] = now
        }
        if (seenAt.size > 64) {
            val cutoff = now - lockoutSeconds * 10
            seenAt.entries.removeAll { it.value <= cutoff }
        }
        return fresh
    }

    private fun arm(uid: String, input: InputSource) {
        armedUid = uid
        armedName = keyName(uid)
        state = WallState.ARMED
        stateSince = now
        input.clearButton() // ignore any press from before the tap

        val cue = cues.openerSound("welcome", armedName) ?: cues.armedCue
        speakingUntil = now + (cue?.durationSeconds ?: 0.0)
        if (cue != null) pendingEvents.add(GameEvent.PlayAnnouncement(cue))
        Log.i(TAG, "$uid: welcomed $armedName, waiting for the button")
    }

    /** (prize, wasRigged) for this tap, and remembers the key was used.
     * A rigged key pays out its prize only on its first tap; after that, or if
     * its prize is already gone, it falls through to the ordinary random draw. */
    private fun resolve(uid: String): Pair<Prize?, Boolean> {
        val riggedId = config.riggedTags[uid]
        val firstUse = uid !in usedKeys
        if (riggedId != null && firstUse && riggedId !in claimed) {
            usedKeys.add(uid)
            return byId.getValue(riggedId) to true
        }
        val prize = pickRandomPrize()
        usedKeys.add(uid)
        return prize to false
    }

    private fun log(uid: String, prize: Prize, rigged: Boolean) {
        val stamp = LocalDateTime.now().format(DateTimeFormatter.ISO_LOCAL_DATE_TIME)
        val line = "$stamp\t$uid\t${prize.id}\t${prize.name}\t${if (rigged) "rigged" else "random"}\t" +
            "${available().size} left"
        Log.i(TAG, line)
        logger.log(line)
    }

    private fun beginSpin(uid: String) {
        // Every frame stays in the shuffle, evicted or not — the target is
        // always a still-available prize (resolve/pickRandomPrize only ever
        // draw from openToAll), so an evicted tile can flash past but never
        // be landed on. Restricting the pool to what's left made the
        // endgame look broken: two prizes flickering back and forth between
        // just each other, or one prize sitting there dinging alone once
        // only it remained.
        val active = prizes.map { it.index }
        val (prize, rigged) = resolve(uid)
        if (prize == null) {
            Log.i(TAG, "$uid: nothing left on the wall for an ordinary key.")
            state = WallState.EXHAUSTED
            stateSince = now
            return
        }

        winner = prize
        spinActive = active
        val target = prize.index

        spinTicks = minTicks
        spinSequence = buildRoute(active, target, spinTicks)
        spinStepShown = null

        if (onceFerNight) claimed.add(prize.id)
        saveState()

        state = WallState.SPINNING
        stateSince = now
        if (cues.idleMusic != null) pendingEvents.add(GameEvent.FadeIdleMusic)
        cues.startCue?.let { pendingEvents.add(GameEvent.Play(it)) }
        if (cues.loopCue != null) pendingEvents.add(GameEvent.StartLoop)
        log(uid, prize, rigged)
    }

    /** A shuffled tour of `active` of length ticks+1, ending on `target`.
     * Laid out as back-to-back shuffles of the available tiles so every prize
     * still gets visited about equally often, without ever going round in grid
     * order; consecutive stops are never the same tile. */
    private fun buildRoute(active: List<Int>, target: Int, ticks: Int): List<Int> {
        val route = mutableListOf<Int>()
        var last: Int? = null
        while (route.size <= ticks) {
            val block = active.shuffled(random).toMutableList()
            if (block.size > 1 && block.first() == last) {
                val tmp = block[0]; block[0] = block[block.size - 1]; block[block.size - 1] = tmp
            }
            route.addAll(block)
            last = block.last()
        }
        val trimmed = route.subList(0, ticks + 1).toMutableList()
        trimmed[ticks] = target
        if (ticks > 0 && trimmed[ticks - 1] == target && active.size > 1) {
            trimmed[ticks - 1] = active.first { it != target }
        }
        return trimmed
    }

    /** Which stop on the route the shuffle has reached. */
    private fun spinStep(): Int {
        val p = min(1.0, (now - stateSince) / spinSeconds)
        return min(spinTicks, floor(spinCurve(p) * spinTicks).toInt())
    }

    /** Index into `prizes` of the tile lit right now. */
    fun highlightIndex(): Int = spinSequence[spinStep()]

    private fun spinAudio() {
        val step = spinStep()
        if (step != spinStepShown) {
            spinStepShown = step
            cues.tickCue?.let { pendingEvents.add(GameEvent.Play(it)) }
        }
    }

    private fun finishSpin() {
        val currentWinner = winner
        if (currentWinner == null) {
            backToIdle()
            return
        }
        state = WallState.LANDED
        stateSince = now
        if (cues.loopCue != null) pendingEvents.add(GameEvent.StopLoop)
        cues.bellCue?.let { pendingEvents.add(GameEvent.Play(it)) }
    }

    private fun beginReveal() {
        state = WallState.REVEAL
        stateSince = now

        val prize = winner!!
        val prizeCue = cues.prizeSound(prize)
        val cue = prizeCue ?: cues.revealCue
        val opener = if (prizeCue != null && prize.opener != "none") {
            cues.openerSound(prize.opener, armedName)
        } else null

        var spoken = 0.0
        when {
            opener != null && cue != null -> {
                pendingEvents.add(GameEvent.PlayQueued(opener, cue))
                spoken = opener.durationSeconds + cue.durationSeconds
            }
            cue != null -> {
                pendingEvents.add(GameEvent.PlayAnnouncement(cue))
                spoken = cue.durationSeconds
            }
        }

        revealUntil = now + maxOf(revealHoldSeconds, spoken + revealTailSeconds)
    }

    private fun backToIdle() {
        armedUid = null
        winner = null
        state = if (openToAll().isNotEmpty()) WallState.IDLE else WallState.EXHAUSTED
        stateSince = now
        if (cues.idleMusic != null && state == WallState.IDLE) {
            pendingEvents.add(GameEvent.StartIdleMusic)
        }
    }
}
