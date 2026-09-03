package org.prizeandroid.wall.game

/** Fraction of the spin's speed that never decays away, so the shuffle keeps visibly moving. */
private const val SPIN_TAIL = 0.15

/** 0..1 -> 0..1, fast at the start, slow but never stopped at the end. */
fun spinCurve(p: Double): Double =
    (1.0 - SPIN_TAIL) * (1.0 - Math.pow(1.0 - p, 4.0)) + SPIN_TAIL * p

fun easeOut(p: Double): Double = 1.0 - Math.pow(1.0 - p, 4.0)
