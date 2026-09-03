package org.prizeandroid.wall.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.util.Log
import java.util.ArrayDeque
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine

private const val TAG = "WirelessLink"

sealed class LinkStatus {
    data object Disconnected : LinkStatus()
    data object Scanning : LinkStatus()
    data object Connecting : LinkStatus()
    data class Connected(val identity: String?) : LinkStatus()
}

/**
 * Kotlin port of reader.py's `_LinkCore` + `BleLink`.
 *
 * The wireless unit (firmware/prize_reader_ble) is a Nordic UART Service
 * peripheral sending newline-terminated ASCII lines:
 *
 *   V <text>   identifies itself, once, on connect
 *   T <uid>    a tag is on the reader, repeated ~7x/sec while it stays there
 *   B          the button was pressed
 *   H          heartbeat
 *
 * `T` repeating is the contract, not chattiness: the caller decides a key has
 * been lifted by not seeing it for a while (see `rescan_lockout_seconds` in
 * the game state machine), so a unit announcing each tag once would silently
 * hand out extra turns.
 *
 * Pull-based API (`readTag`/`takeButton`) mirrors the Python class so the
 * game loop that consumes it can be a straightforward port too.
 */
class WirelessLink(
    private val context: Context,
    private val deviceName: String,
    private val deviceAddress: String?,
    reconnectSeconds: Double,
) {
    private val reconnectMillis = (reconnectSeconds * 1000).toLong().coerceAtLeast(500)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var job: Job? = null
    private var gatt: BluetoothGatt? = null

    private val tagLock = Any()
    private val tags = ArrayDeque<String>()
    private val buttons = ArrayDeque<Boolean>()

    @Volatile private var lastLineAt = 0L
    @Volatile private var partial = ""
    @Volatile private var identity: String? = null

    private val _status = MutableStateFlow<LinkStatus>(LinkStatus.Disconnected)
    val status: StateFlow<LinkStatus> = _status

    /** The most recent raw tag UID seen, kept for display (e.g. so a UID can be
     * read off screen to fill in `key_names`/`rigged_tags`). This is a
     * non-consuming side-channel: reading it never affects `readTag()`'s queue. */
    private val _lastTagSeen = MutableStateFlow<String?>(null)
    val lastTagSeen: StateFlow<String?> = _lastTagSeen

    /** Same 10s staleness window as the Python `connected` property. */
    val connected: Boolean
        get() = _status.value is LinkStatus.Connected &&
            lastLineAt != 0L &&
            SystemClock.elapsedRealtime() - lastLineAt < 10_000

    fun readTag(): String? = synchronized(tagLock) { tags.pollFirst() }

    fun takeButton(): Boolean = synchronized(tagLock) {
        if (buttons.isEmpty()) return false
        buttons.clear()
        true
    }

    fun clearButton() = synchronized(tagLock) { buttons.clear() }

    fun start() {
        if (job != null) return
        job = scope.launch { runLoop() }
    }

    fun close() {
        job?.cancel()
        job = null
        closeGattQuietly()
        _status.value = LinkStatus.Disconnected
    }

    // -- connection lifecycle -------------------------------------------------

    private suspend fun runLoop() {
        while (scope.isActive) {
            try {
                connectOnce()
            } catch (e: Exception) {
                Log.w(TAG, "BLE link: ${e.message}")
            }
            _status.value = LinkStatus.Disconnected
            if (scope.isActive) delay(reconnectMillis)
        }
    }

    @SuppressLint("MissingPermission")
    private suspend fun connectOnce() {
        val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)
            ?.adapter
        if (adapter == null || !adapter.isEnabled) {
            throw IllegalStateException("Bluetooth is off or unsupported")
        }

        val device = if (!deviceAddress.isNullOrBlank()) {
            adapter.getRemoteDevice(deviceAddress)
        } else {
            _status.value = LinkStatus.Scanning
            scanForName(adapter, deviceName)
                ?: throw IllegalStateException("no unit advertising as '$deviceName'")
        }

        _status.value = LinkStatus.Connecting
        connectAndListen(device)
    }

    @SuppressLint("MissingPermission")
    private suspend fun scanForName(adapter: BluetoothAdapter, name: String): BluetoothDevice? {
        val scanner = adapter.bluetoothLeScanner ?: return null
        return suspendCancellableCoroutine { cont ->
            var resolved = false
            val callback = object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) {
                    val seenName = result.scanRecord?.deviceName ?: result.device.name
                    if (!resolved && seenName == name) {
                        resolved = true
                        try { scanner.stopScan(this) } catch (_: Exception) {}
                        if (cont.isActive) cont.resumeWith(Result.success(result.device))
                    }
                }
                override fun onScanFailed(errorCode: Int) {
                    if (!resolved && cont.isActive) {
                        cont.resumeWith(Result.success(null))
                    }
                }
            }
            val settings = ScanSettings.Builder()
                .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
                .build()
            scanner.startScan(emptyList<ScanFilter>(), settings, callback)
            cont.invokeOnCancellation { try { scanner.stopScan(callback) } catch (_: Exception) {} }
            scope.launch {
                delay(8_000)
                if (!resolved) {
                    resolved = true
                    try { scanner.stopScan(callback) } catch (_: Exception) {}
                    if (cont.isActive) cont.resumeWith(Result.success(null))
                }
            }
        }
    }

    @SuppressLint("MissingPermission")
    private suspend fun connectAndListen(device: BluetoothDevice) {
        suspendCancellableCoroutine<Unit> { cont ->
            var subscribed = false
            var watchdog: Job? = null

            fun finish(error: Throwable?) {
                watchdog?.cancel()
                if (cont.isActive) {
                    if (error != null) cont.resumeWith(Result.failure(error))
                    else cont.resumeWith(Result.success(Unit))
                }
            }

            val callback = object : BluetoothGattCallback() {
                override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
                    if (newState == BluetoothProfile.STATE_CONNECTED) {
                        g.discoverServices()
                    } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                        val reason = if (status != BluetoothGatt.GATT_SUCCESS) {
                            IllegalStateException("gatt status $status")
                        } else null
                        finish(reason)
                    }
                }

                override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
                    if (status != BluetoothGatt.GATT_SUCCESS) {
                        finish(IllegalStateException("service discovery failed ($status)"))
                        g.disconnect()
                        return
                    }
                    val service = g.getService(Nus.SERVICE)
                    val txChar = service?.getCharacteristic(Nus.TX)
                    if (service == null || txChar == null) {
                        finish(IllegalStateException("unit has no NUS service"))
                        g.disconnect()
                        return
                    }
                    g.setCharacteristicNotification(txChar, true)
                    val cccd = txChar.getDescriptor(Nus.CCCD)
                    if (cccd != null) {
                        @Suppress("DEPRECATION")
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                            g.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                        } else {
                            cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                            g.writeDescriptor(cccd)
                        }
                    }
                    subscribed = true
                    lastLineAt = SystemClock.elapsedRealtime()
                    partial = ""
                    _status.value = LinkStatus.Connected(identity)
                    Log.i(TAG, "Wireless unit over BLE ($deviceName)")

                    watchdog = scope.launch {
                        while (isActive) {
                            delay(1_000)
                            if (SystemClock.elapsedRealtime() - lastLineAt > 10_000) {
                                Log.w(TAG, "unit has gone quiet; reconnecting")
                                g.disconnect()
                                break
                            }
                        }
                    }
                }

                override fun onCharacteristicChanged(
                    g: BluetoothGatt,
                    characteristic: BluetoothGattCharacteristic,
                ) {
                    @Suppress("DEPRECATION")
                    feed(characteristic.value ?: return)
                }

                override fun onCharacteristicChanged(
                    g: BluetoothGatt,
                    characteristic: BluetoothGattCharacteristic,
                    value: ByteArray,
                ) {
                    feed(value)
                }
            }

            gatt = device.connectGatt(context, false, callback, BluetoothDevice.TRANSPORT_LE)
            cont.invokeOnCancellation {
                watchdog?.cancel()
                closeGattQuietly()
            }
        }
        closeGattQuietly()
    }

    @SuppressLint("MissingPermission")
    private fun closeGattQuietly() {
        try { gatt?.disconnect() } catch (_: Exception) {}
        try { gatt?.close() } catch (_: Exception) {}
        gatt = null
    }

    // -- line protocol, byte-identical contract to _LinkCore.feed/_handle ----

    private fun feed(data: ByteArray) {
        partial += String(data, Charsets.US_ASCII)
        while (true) {
            val idx = partial.indexOf('\n')
            if (idx < 0) break
            val line = partial.substring(0, idx).trim()
            partial = partial.substring(idx + 1)
            handle(line)
        }
    }

    private fun handle(line: String) {
        if (line.isEmpty()) return
        lastLineAt = SystemClock.elapsedRealtime()
        val spaceIdx = line.indexOf(' ')
        val kind = if (spaceIdx >= 0) line.substring(0, spaceIdx) else line
        val rest = if (spaceIdx >= 0) line.substring(spaceIdx + 1).trim() else ""
        when (kind) {
            "T" -> if (rest.isNotEmpty()) {
                val uid = rest.uppercase()
                pushTag(uid)
                _lastTagSeen.value = uid
            }
            "B" -> pushButton()
            "V" -> {
                identity = rest
                _status.value = LinkStatus.Connected(rest)
                Log.i(TAG, "wireless unit: $rest")
            }
            // H is a heartbeat; the timestamp bump above is all it needs.
        }
    }

    private fun pushTag(uid: String) = synchronized(tagLock) {
        tags.addLast(uid)
        while (tags.size > 4) tags.pollFirst()
    }

    private fun pushButton() = synchronized(tagLock) {
        buttons.addLast(true)
        while (buttons.size > 2) buttons.pollFirst()
    }
}

private object Nus {
    val SERVICE: UUID = UUID.fromString("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
    val RX: UUID = UUID.fromString("6e400002-b5a3-f393-e0a9-e50e24dcca9e") // write to device
    val TX: UUID = UUID.fromString("6e400003-b5a3-f393-e0a9-e50e24dcca9e") // device notifies us
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
}
