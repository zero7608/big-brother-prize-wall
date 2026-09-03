package org.prizeandroid.wall.game

import android.content.Context
import java.io.File
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Same three fields prize_wall.py writes to night_state.json. */
@Serializable
data class NightStateData(
    val claimed: List<String> = emptyList(),
    @SerialName("used_keys") val usedKeys: List<String> = emptyList(),
    @SerialName("wall_order") val wallOrder: List<String> = emptyList(),
)

interface NightStateStore {
    fun load(): NightStateData?
    fun save(data: NightStateData)
    fun clear()
}

/** Ports `_load_state`/`_save_state`/`reset_night`'s file handling, using
 * app-private internal storage instead of a path next to the script. */
class FileNightStateStore(
    context: Context,
    fileName: String = "night_state.json",
) : NightStateStore {
    private val file = File(context.filesDir, fileName)
    private val json = Json { ignoreUnknownKeys = true; isLenient = true; prettyPrint = true }

    override fun load(): NightStateData? {
        if (!file.exists()) return null
        return runCatching { json.decodeFromString(NightStateData.serializer(), file.readText()) }
            .getOrNull()
    }

    override fun save(data: NightStateData) {
        file.writeText(json.encodeToString(NightStateData.serializer(), data))
    }

    override fun clear() {
        if (file.exists()) file.delete()
    }
}

interface ScanLogger {
    fun log(line: String)
}

/** Ports `PrizeWall.log`'s append-to-file half; the print() half becomes Log.i at the call site. */
class FileScanLogger(
    context: Context,
    fileName: String = "scans.log",
) : ScanLogger {
    private val file = File(context.filesDir, fileName)

    override fun log(line: String) {
        file.appendText(line + "\n")
    }
}
