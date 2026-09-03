package org.prizeandroid.wall.config

import android.content.Context
import kotlinx.serialization.json.Json

private val json = Json {
    ignoreUnknownKeys = true
    isLenient = true
}

/** Loads config.json from the app's assets, mirroring how prize_wall.py reads it at startup. */
object ConfigLoader {
    fun load(context: Context, assetName: String = "config.json"): WallConfig {
        val text = context.assets.open(assetName).bufferedReader().use { it.readText() }
        return json.decodeFromString(WallConfig.serializer(), text)
    }
}
