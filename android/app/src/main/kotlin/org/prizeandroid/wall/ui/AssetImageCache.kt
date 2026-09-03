package org.prizeandroid.wall.ui

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap

/** Decodes `assets/images/<name>` on first use and keeps it in memory,
 * mirroring `Prize._load_image`'s "load once, ignore if missing" behaviour. */
class AssetImageCache(private val context: Context) {
    private val cache = mutableMapOf<String, ImageBitmap?>()

    fun get(name: String?): ImageBitmap? {
        if (name.isNullOrBlank()) return null
        return cache.getOrPut(name) {
            runCatching {
                context.assets.open("images/$name").use { stream ->
                    BitmapFactory.decodeStream(stream)?.asImageBitmap()
                }
            }.getOrNull()
        }
    }
}
