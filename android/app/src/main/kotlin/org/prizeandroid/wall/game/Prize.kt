package org.prizeandroid.wall.game

import org.prizeandroid.wall.config.PrizeConfig

/** Runtime prize model: `index` is the fixed position in config.json's `prizes` array. */
data class Prize(
    val index: Int,
    val id: String,
    val name: String,
    val image: String?,
    val soundName: String?,
    val weight: Double,
    val opener: String,
) {
    companion object {
        fun from(index: Int, cfg: PrizeConfig) = Prize(
            index = index,
            id = cfg.id,
            name = cfg.name,
            image = cfg.image,
            soundName = cfg.sound,
            weight = cfg.weight,
            opener = cfg.opener,
        )
    }
}
