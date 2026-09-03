package org.prizeandroid.wall.game

import android.content.Context
import android.os.SystemClock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.prizeandroid.wall.audio.AudioController
import org.prizeandroid.wall.audio.SoundAssetLibrary
import org.prizeandroid.wall.ble.LinkStatus
import org.prizeandroid.wall.ble.WirelessLink
import org.prizeandroid.wall.config.WallConfig

/** One prize's frame on the wall, as the renderer needs it: where it sits,
 * whether it should render greyed-out-and-crossed-out, and whether it is lit
 * right now (spinning highlight, the landed winner, or the reveal). */
data class TileRenderState(
    val prizeId: String,
    val name: String,
    val image: String?,
    val slot: Int,
    val evicted: Boolean,
    val lit: Boolean,
)

/** What the UI needs to render, snapshotted once per tick. Audio (Phase F)
 * will subscribe to the engine's GameEvents separately. */
data class EngineSnapshot(
    val state: WallState,
    val now: Double,
    val stateSince: Double,
    val speakingUntil: Double,
    val armedName: String,
    val winnerName: String?,
    val winnerImage: String?,
    val availableCount: Int,
    val totalCount: Int,
    val linkStatus: LinkStatus,
    val lastTagSeen: String?,
    val tiles: List<TileRenderState>,
)

/**
 * Glues `PrizeWallEngine` to a real `WirelessLink` and runs the per-frame
 * tick loop, the same job `PrizeWall.run()` does in prize_wall.py. Owns
 * nothing UI-specific so it can be swapped for a Compose ViewModel wrapper
 * later without touching the engine or the link.
 */
class PrizeWallController(private val context: Context, private val config: WallConfig) {
    // Kept lightweight: WirelessLink/CombinedInputSource do no blocking IO, so
    // these are safe to construct on whichever thread creates the controller
    // (Compose's `remember {}`, i.e. the main thread). Everything that DOES
    // block — SoundAssetLibrary's cue loading — is built inside `start()`'s
    // background coroutine instead: SoundPool always delivers its
    // load-complete callback on the main Looper regardless of which thread
    // created the pool, so blocking the main thread waiting for it would
    // deadlock. See SoundAssetLibrary's `loadCue`.
    private val link = WirelessLink(
        context = context,
        deviceName = config.reader.name,
        deviceAddress = config.reader.address.ifBlank { null },
        reconnectSeconds = config.reader.reconnectSeconds,
    )
    val input = CombinedInputSource(link)

    private lateinit var audio: AudioController
    private lateinit var engine: PrizeWallEngine

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null
    private val startedAt = SystemClock.elapsedRealtime()

    private val _snapshot = MutableStateFlow<EngineSnapshot?>(null)
    val snapshot: StateFlow<EngineSnapshot?> = _snapshot

    fun start() {
        link.start()
        if (job != null) return
        job = scope.launch {
            val prizesForAudio = config.prizes.mapIndexed { i, spec -> Prize.from(i, spec) }
            val audioLibrary = SoundAssetLibrary(context, config.spin, prizesForAudio)
            audio = AudioController(context, audioLibrary)
            engine = PrizeWallEngine(
                config = config,
                cues = audioLibrary,
                stateStore = FileNightStateStore(context),
                logger = FileScanLogger(context),
            )

            while (isActive) {
                val now = (SystemClock.elapsedRealtime() - startedAt) / 1000.0
                val events = engine.tick(now, input)
                if (events.isNotEmpty()) audio.handle(events)
                _snapshot.value = snapshotNow()
                delay(50)
            }
        }
    }

    fun close() {
        job?.cancel()
        job = null
        link.close()
        if (::audio.isInitialized) audio.release()
    }

    fun resetNight() {
        if (::engine.isInitialized) engine.resetNight()
    }

    private fun snapshotNow(): EngineSnapshot {
        val litIndex = when (engine.state) {
            WallState.SPINNING -> engine.highlightIndex()
            WallState.LANDED, WallState.REVEAL -> engine.winner?.index
            else -> null
        }
        val tiles = engine.prizes.map { prize ->
            TileRenderState(
                prizeId = prize.id,
                name = prize.name,
                image = prize.image,
                slot = engine.slotOf(prize.index),
                evicted = engine.isEvicted(prize),
                lit = prize.index == litIndex,
            )
        }.sortedBy { it.slot }
        return EngineSnapshot(
            state = engine.state,
            now = engine.now,
            stateSince = engine.stateSince,
            speakingUntil = engine.speakingUntil,
            armedName = engine.armedName,
            winnerName = engine.winner?.name,
            winnerImage = engine.winner?.image,
            availableCount = engine.available().size,
            totalCount = engine.prizes.size,
            linkStatus = link.status.value,
            lastTagSeen = link.lastTagSeen.value,
            tiles = tiles,
        )
    }
}
