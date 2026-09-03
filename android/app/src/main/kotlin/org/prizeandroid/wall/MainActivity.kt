package org.prizeandroid.wall

import android.Manifest
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.prizeandroid.wall.config.ConfigLoader
import org.prizeandroid.wall.config.WallConfig
import org.prizeandroid.wall.game.PrizeWallController
import org.prizeandroid.wall.ui.PrizeWallScreen

/** BLE runtime permissions, split by API level the same way the manifest is. */
private val blePermissions: Array<String> =
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    } else {
        arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
    }

/** Kiosk-style host: fullscreen, screen-on, hidden system bars. */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enableEdgeToEdge()
        hideSystemBars()

        setContent {
            PrizeWallRoot()
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    }
}

@Composable
fun PrizeWallRoot() {
    val context = LocalContext.current
    val config = remember { ConfigLoader.load(context) }

    var hasBlePermission by remember { mutableStateOf(false) }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants -> hasBlePermission = grants.values.all { it } }

    LaunchedEffect(Unit) { permissionLauncher.launch(blePermissions) }

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize(), color = Color.Black) {
            Box(
                modifier = Modifier.fillMaxSize().background(Color.Black),
                contentAlignment = Alignment.Center
            ) {
                if (hasBlePermission) {
                    WallHost(config)
                } else {
                    Text(text = "Bluetooth permission needed", color = Color.White)
                }
            }
        }
    }
}

/** Hosts the real wall renderer plus a corner strip of simulate buttons —
 * the on-screen analogue of prize_wall.py's `--simulate`/`--keys` taps — so
 * the whole flow can be exercised without the physical BLE reader unit. */
@Composable
private fun WallHost(config: WallConfig) {
    val context = LocalContext.current
    val controller = remember { PrizeWallController(context, config) }
    DisposableEffect(controller) {
        controller.start()
        onDispose { controller.close() }
    }

    val snapshot by controller.snapshot.collectAsStateWithLifecycle()
    val current = snapshot

    if (current == null) {
        // SoundAssetLibrary is still decoding the cue set on a background
        // thread (see PrizeWallController.start) — brief on a small sound
        // pack, but real until the first tick lands.
        Text(text = "Loading…", color = Color.White)
        return
    }

    Box(modifier = Modifier.fillMaxSize()) {
        PrizeWallScreen(config = config, snapshot = current, modifier = Modifier.fillMaxSize())

        // Debug-only: simulate taps without the physical reader, and read a
        // real tag's UID off screen to fill in config.json's key_names/
        // rigged_tags. Stripped from a release build automatically.
        if (BuildConfig.DEBUG) {
            Column(
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .background(Color.Black.copy(alpha = 0.35f))
                    .padding(4.dp),
            ) {
                Row {
                    config.riggedTags.keys.take(3).forEachIndexed { i, uid ->
                        Button(onClick = { controller.input.simulateTag(uid) }) { Text("Rig ${i + 1}") }
                    }
                    Button(onClick = { controller.input.simulateTag("SIM-${System.currentTimeMillis()}") }) {
                        Text("Tag")
                    }
                    Button(onClick = { controller.input.simulateButtonPress() }) { Text("Button") }
                    Button(onClick = { controller.resetNight() }) { Text("Reset") }
                }
                Text(text = "last tag UID: ${current.lastTagSeen ?: "-"}", color = Color.White)
            }
        }
    }
}
