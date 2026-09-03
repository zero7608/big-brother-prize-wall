package org.prizeandroid.wall.audio

import android.content.Context
import android.media.MediaPlayer
import android.util.Log
import java.util.Collections
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.prizeandroid.wall.game.Cue
import org.prizeandroid.wall.game.GameEvent

private const val TAG = "AudioController"

/**
 * Carries out the `GameEvent`s the engine emits from `tick()`. Which player
 * handles a cue is decided by the engine's choice of event, not by measuring
 * the clip: `Play` is always one of the four fixed, short sound effects
 * (tick/bell/start/loop) and goes through the `SoundPool` `SoundAssetLibrary`
 * loaded them into, for the low latency a rapid string of ticks needs.
 * `PlayAnnouncement`/`PlayQueued` are always a welcome greeting or a prize's
 * reveal line — unpredictable length, a couple of seconds to Zingbot's ~10s
 * joke — and always go through `MediaPlayer` instead. Routing by intent
 * rather than a measured duration threshold matters because WAV duration
 * metadata is exactly the kind of thing that reads unreliably on some
 * devices; getting that measurement wrong must never silently put a long
 * clip back on the pool player that truncates it.
 */
class AudioController(private val context: Context, private val library: SoundAssetLibrary) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loopStreamId = 0
    private var idlePlayer: MediaPlayer? = null

    /** A `MediaPlayer` with no reference held anywhere else is fair game for
     * garbage collection mid-playback — its native player gets torn down and
     * the clip stops dead, and a longer clip has much better odds of getting
     * caught by a GC pass than a short one, which is exactly the pattern this
     * fixes (Zingbot's ~10s line, Slop's ~6.5s one, both stopping early). This
     * set is that reference, for every announcement clip currently playing. */
    private val activePlayers = Collections.newSetFromMap(java.util.concurrent.ConcurrentHashMap<MediaPlayer, Boolean>())

    private fun track(player: MediaPlayer) {
        activePlayers.add(player)
    }

    private fun untrackAndRelease(player: MediaPlayer) {
        activePlayers.remove(player)
        runCatching { player.release() }
    }

    fun handle(events: List<GameEvent>) {
        for (event in events) {
            when (event) {
                is GameEvent.Play -> play(event.cue)
                is GameEvent.PlayAnnouncement -> scope.launch { playAnnouncement(event.cue) }
                is GameEvent.PlayQueued -> scope.launch { playQueued(event.first, event.second) }
                GameEvent.StartLoop -> startLoop()
                GameEvent.StopLoop -> stopLoop()
                GameEvent.StartIdleMusic -> startIdleMusic()
                GameEvent.FadeIdleMusic -> fadeIdleMusic()
            }
        }
    }

    fun release() {
        scope.cancel()
        stopIdlePlayer()
        activePlayers.forEach { runCatching { it.release() } }
        activePlayers.clear()
        library.release()
    }

    private fun play(cue: Cue) {
        val id = library.soundIdFor(cue) ?: return
        library.pool().play(id, 1f, 1f, 1, 0, 1f)
    }

    private fun playAnnouncement(cue: Cue) {
        try {
            val player = MediaPlayer()
            track(player)
            context.assets.openFd("sounds/${cue.assetPath}").use { afd ->
                player.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            }
            player.setOnCompletionListener { untrackAndRelease(it) }
            player.setOnErrorListener { mp, _, _ -> untrackAndRelease(mp); true }
            player.prepare()
            player.start()
        } catch (e: Exception) {
            Log.w(TAG, "announcement cue '${cue.assetPath}' failed: ${e.message}")
        }
    }

    /** `first` then `second`, heard as one sentence — pygame's `channel.queue()`
     * equivalent, via `MediaPlayer.setNextMediaPlayer` for a real gapless handoff. */
    private fun playQueued(first: Cue, second: Cue) {
        try {
            val p1 = MediaPlayer()
            val p2 = MediaPlayer()
            track(p1)
            track(p2)
            context.assets.openFd("sounds/${first.assetPath}").use { afd ->
                p1.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            }
            context.assets.openFd("sounds/${second.assetPath}").use { afd ->
                p2.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            }
            p1.prepare()
            p2.prepare()
            p1.setNextMediaPlayer(p2)
            p1.setOnCompletionListener { untrackAndRelease(it) }
            p2.setOnCompletionListener { untrackAndRelease(it) }
            p1.setOnErrorListener { mp, _, _ -> untrackAndRelease(mp); true }
            p2.setOnErrorListener { mp, _, _ -> untrackAndRelease(mp); true }
            p1.start()
        } catch (e: Exception) {
            Log.w(TAG, "queued cue '${first.assetPath}'+'${second.assetPath}' failed: ${e.message}")
        }
    }

    private fun startLoop() {
        val cue = library.loopCue ?: return
        val id = library.soundIdFor(cue) ?: return
        loopStreamId = library.pool().play(id, 1f, 1f, 1, -1, 1f)
    }

    private fun stopLoop() {
        if (loopStreamId != 0) {
            library.pool().stop(loopStreamId)
            loopStreamId = 0
        }
    }

    private fun startIdleMusic() {
        val cue = library.idleMusic ?: return
        stopIdlePlayer()
        val player = MediaPlayer()
        try {
            context.assets.openFd("sounds/${cue.assetPath}").use { afd ->
                player.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
            }
            player.isLooping = true
            player.setVolume(1f, 1f)
            player.prepare()
            player.start()
            idlePlayer = player
        } catch (e: Exception) {
            Log.w(TAG, "idle music failed: ${e.message}")
        }
    }

    /** A 400ms fade rather than a hard cut, same as `pygame.mixer.music.fadeout(400)`. */
    private fun fadeIdleMusic() {
        val player = idlePlayer ?: return
        idlePlayer = null // ownership moves to the fade coroutine
        scope.launch {
            val steps = 8
            for (i in steps downTo 0) {
                val v = i / steps.toFloat()
                runCatching { player.setVolume(v, v) }
                delay(50)
            }
            runCatching { player.stop(); player.release() }
        }
    }

    private fun stopIdlePlayer() {
        idlePlayer?.let { runCatching { it.stop(); it.release() } }
        idlePlayer = null
    }
}
