package org.prizeandroid.wall.audio

import android.content.Context
import android.content.res.AssetFileDescriptor
import android.content.res.AssetManager
import android.media.AudioAttributes
import android.media.MediaMetadataRetriever
import android.media.MediaPlayer
import android.media.SoundPool
import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.prizeandroid.wall.config.SpinConfig
import org.prizeandroid.wall.game.Cue
import org.prizeandroid.wall.game.CueLibrary
import org.prizeandroid.wall.game.Prize
import org.prizeandroid.wall.game.nameSlug

private const val TAG = "SoundAssetLibrary"

/**
 * Loads cues from `assets/sounds/` into a `SoundPool`, mirroring
 * `Prize._load_sound`/`PrizeWall.opener_sound`: a missing file is logged and
 * treated as absent rather than failing the show, and an opener family/guest
 * lookup tries `family_slug_1..9.wav`, then `family_slug.wav`, then falls
 * back to the unnamed `family_1..9.wav`/`family.wav` takes, caching the
 * result the same way `self._openers` does.
 *
 * `loadCue` blocks the calling thread until SoundPool has actually finished
 * decoding, the same way pygame.mixer.Sound(path) blocks until fully decoded
 * — SoundPool's own `load()` returns before that, and playing a cue before
 * its decode completes is a silent no-op, which otherwise bites exactly the
 * lazily-loaded opener clips (welcome/congrats/badnews) since they're loaded
 * and played back to back the first time a given guest is heard. Fixed cues
 * are loaded eagerly at construction time; if a much larger sound pack makes
 * that noticeably slow, move construction off the main thread.
 */
class SoundAssetLibrary(
    context: Context,
    spin: SpinConfig,
    prizes: List<Prize>,
) : CueLibrary {

    private val assetManager: AssetManager = context.assets
    private val availableFiles: Set<String> = (assetManager.list("sounds") ?: emptyArray()).toSet()
    private val soundPool: SoundPool = SoundPool.Builder()
        .setMaxStreams(16)
        .setAudioAttributes(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_GAME)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
        )
        .build()

    private val cueCache = mutableMapOf<String, Cue?>()
    private val soundIdByAsset = mutableMapOf<String, Int>()
    private val openedDescriptors = mutableListOf<AssetFileDescriptor>()
    private val openerCache = mutableMapOf<Pair<String, String>, List<Cue>>()

    /** `SoundPool.load()` decodes off-thread and returns before it's actually
     * ready; playing a cue too soon after loading it is a silent no-op. This
     * bridges that async completion back to a synchronous wait, so `loadCue`
     * behaves like pygame's `Sound(path)`, which blocks until fully decoded. */
    private val pendingLoads = ConcurrentHashMap<Int, CountDownLatch>()

    init {
        soundPool.setOnLoadCompleteListener { _, sampleId, _ ->
            pendingLoads.remove(sampleId)?.countDown()
        }
    }

    override val startCue: Cue? = loadCue(spin.startSound)
    override val loopCue: Cue? = loadCue(spin.loopSound)
    override val tickCue: Cue? = loadCue(spin.tickSound)
    override val bellCue: Cue? = loadCue(spin.bellSound)
    override val armedCue: Cue? = loadCue(spin.armedSound)
    override val revealCue: Cue? = loadCue(spin.revealSound)
    override val idleMusic: Cue? = loadCue(spin.idleMusic)

    private val prizeCues: Map<String, Cue?> = prizes.associate { it.id to loadCue(it.soundName) }

    init {
        val loaded = listOfNotNull(startCue, loopCue, tickCue, bellCue, armedCue, revealCue, idleMusic).size +
            prizeCues.values.count { it != null }
        Log.i(TAG, "AUDIO: $loaded cue(s) loaded from ${availableFiles.size} file(s) in assets/sounds")
    }

    override fun prizeSound(prize: Prize): Cue? = prizeCues[prize.id]

    override fun openerSound(family: String, guestName: String): Cue? {
        val key = family to nameSlug(guestName)
        val choices = openerCache.getOrPut(key) { findOpenerTakes(family, key.second) }
        return choices.randomOrNull()
    }

    /** SoundPool's stream id for a cue this library loaded, if it decoded successfully. */
    fun soundIdFor(cue: Cue): Int? = soundIdByAsset[cue.assetPath]

    fun pool(): SoundPool = soundPool

    fun release() {
        soundPool.release()
        openedDescriptors.forEach { runCatching { it.close() } }
        openedDescriptors.clear()
    }

    private fun findOpenerTakes(family: String, slug: String): List<Cue> {
        for (stem in listOf("${family}_$slug", family)) {
            val takes = (1..9).mapNotNull { n -> loadCue("${stem}_$n.wav", quiet = true) }
            if (takes.isNotEmpty()) return takes
            val one = loadCue("$stem.wav", quiet = true)
            if (one != null) return listOf(one)
        }
        return emptyList()
    }

    private fun loadCue(name: String?, quiet: Boolean = false): Cue? {
        if (name.isNullOrBlank()) return null
        cueCache[name]?.let { return it }
        if (name !in availableFiles) {
            if (!quiet) Log.w(TAG, "missing sound: sounds/$name")
            return null
        }
        return try {
            val afd = assetManager.openFd("sounds/$name")
            openedDescriptors.add(afd)

            var durationMs = runCatching {
                val retriever = MediaMetadataRetriever()
                retriever.setDataSource(afd.fileDescriptor, afd.startOffset, afd.length)
                val ms = retriever
                    .extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                    ?.toLongOrNull() ?: 0L
                retriever.release()
                ms
            }.getOrDefault(0L)

            // MediaMetadataRetriever is unreliable on plain PCM WAV on some
            // devices and can report 0 for a real multi-second clip; that
            // silently truncates a reveal that's actually still speaking, so
            // fall back to asking a throwaway MediaPlayer for its duration.
            if (durationMs <= 0L) {
                durationMs = runCatching {
                    val probe = MediaPlayer()
                    assetManager.openFd("sounds/$name").use { probeFd ->
                        probe.setDataSource(probeFd.fileDescriptor, probeFd.startOffset, probeFd.length)
                        probe.prepare()
                    }
                    val ms = probe.duration.toLong()
                    probe.release()
                    ms
                }.getOrDefault(0L)
            }

            val latch = CountDownLatch(1)
            val soundId = soundPool.load(afd, 1)
            pendingLoads[soundId] = latch
            if (!latch.await(3, TimeUnit.SECONDS)) {
                Log.w(TAG, "sound $name did not finish loading within 3s; playback may be silent")
            }

            val cue = Cue(name, durationMs / 1000.0)
            cueCache[name] = cue
            soundIdByAsset[name] = soundId
            cue
        } catch (e: Exception) {
            Log.w(TAG, "failed to load sound $name: ${e.message}")
            null
        }
    }
}
