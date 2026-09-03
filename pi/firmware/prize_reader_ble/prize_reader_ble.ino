// Wireless tag reader and button for the memory wall, over Bluetooth LE.
//
// For boards that have BLE but no Bluetooth Classic, which is most of the
// current ones:
//
//   ESP32-C6, C3, S3      BLE only, no Classic, so no serial profile
//   nRF52840 (Nice!Nano)  BLE only
//   ESP32 (Xtensa, WROOM) has Classic; use ../prize_reader instead if you have
//                         one, it is simpler on the Pi side
//
// The transport is the Nordic UART Service, which is the convention for "a
// serial port over BLE": one characteristic the unit notifies on, one the host
// can write to.  The line protocol is byte for byte the same as the serial
// version, so the Pi does not care which it is talking to.
//
// ---------------------------------------------------------------- the protocol
//
//   V <text>       once on connect, identifying the unit
//   T <uid>        a tag is on the reader, REPEATED while it stays there
//   B              the button was pressed
//   E <text>       something is wrong and the unit cannot do its job
//   H              heartbeat, once a second when nothing else is happening
//
// T repeating is the contract, not chattiness.  A key sits on the reader for
// the whole turn, and the wall decides a key has been taken off the pad by not
// having seen it for a few seconds.  That is what stops one guest getting a
// second turn while their key lies there.  Send T once per arrival and the wall
// will hand out extra prizes, and the symptom will look nothing like a protocol
// change.
//
// ------------------------------------------------------------------- the wiring
//
//   ESP32-C6                              PN532 in I2C mode
//     GPIO 6  (SDA)                         SDA        DIP switch 1 on, 2 off
//     GPIO 7  (SCL)                         SCL
//     3V3                                   VCC
//     GND                                   GND
//     GPIO 4  ---- button ---- GND
//
//   Nice!Nano V2 (nRF52840)               PN532 in I2C mode
//     P0.17 (D15)                           SDA
//     P0.22                                 SCL        NOT P0.20: see below
//     3.3V                                   VCC        NOT the RAW pin
//     GND                                    GND
//     P0.06 (D1) ---- button ---- GND
//
// I2C rather than SPI here: both boards have few enough exposed pins that two
// is worth more than four, and the PN532 is nowhere near the speed where SPI
// would matter.  Set the DIP switches accordingly (1 on, 2 off).
//
// Libraries: "Adafruit PN532", and for the nRF52840 the Adafruit nRF52 board
// package (which brings its own BLEUart).  On ESP32 the bundled NimBLE is used.

#include <Adafruit_PN532.h>
#include <Wire.h>

#if defined(ARDUINO_ARCH_NRF52) || defined(ARDUINO_NRF52_ADAFRUIT)
  #include <bluefruit.h>
  #define BOARD_NRF52 1
#else
  #include <NimBLEDevice.h>
  #define BOARD_ESP32 1
#endif

// ------------------------------------------------------------------- settings

static const char *UNIT_NAME = "PrizeShack";

// Pins are given as physical port pins, not Arduino numbers.
//
// D1 on a Nice!Nano, D11 on a Feather and GPIO 6 on an ESP32 can all be the
// same piece of silicon or three different ones, depending on the board
// definition it is compiled against. The port pin does not move. There is no
// nice!nano variant in the Arduino core or in PlatformIO, so this is built
// against the Feather nRF52840 and the labels would otherwise be wrong; the
// lookup below turns a port pin into whatever index this variant calls it.
//
// SCL is P0.22 rather than the P0.20 you might expect from the pad ordering:
// holding P0.20 low at boot forces the Nice!Nano bootloader into DFU, and a
// PN532 that pulls the clock line down while powering up would stop the
// firmware ever starting.
#if defined(BOARD_NRF52)
  static const uint32_t NRF_BUTTON = 6;    // P0.06, the pad marked D1
  static const uint32_t NRF_SDA    = 17;   // P0.17, the pad marked D15
  static const uint32_t NRF_SCL    = 22;   // P0.22, avoiding the DFU pin
#endif

#ifndef BUTTON_PIN
  #define BUTTON_PIN 4                     // ESP32: a plain GPIO number
#endif

#if defined(BOARD_NRF52)
// Find what this variant calls a given port pin. Returns 0xFF if the variant
// does not expose it at all, which is worth knowing rather than silently
// using pin zero.
static uint8_t pinFor(uint32_t portPin) {
  for (uint8_t i = 0; i < PINS_COUNT; i++) {
    if (g_ADigitalPinMap[i] == portPin) return i;
  }
  return 0xFF;
}
static uint8_t PIN_BUTTON = 0xFF;          // resolved in setup()
#else
static const uint8_t PIN_BUTTON = BUTTON_PIN;
#endif

static const uint32_t TAG_REPEAT_MS = 150;  // while a tag sits on the reader
static const uint16_t READ_TIMEOUT_MS = 60; // keeps the button responsive
static const uint32_t DEBOUNCE_MS = 40;
static const uint32_t HEARTBEAT_MS = 1000;

// Nordic UART Service. The Pi looks for exactly these.
#define NUS_SERVICE "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_RX      "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX      "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

Adafruit_PN532 nfc(-1, -1, &Wire);

// Whether the reader answered at startup. A unit that cannot find its PN532
// used to stop dead in setup() and blink: no heartbeat, no error, nothing on
// the link at all, so the wall could not tell a broken reader from a unit out
// of range. It now carries on, advertises, and says what is wrong.
static bool readerOk = false;
static uint32_t lastComplaint = 0;

static uint32_t lastTagSent = 0;
static uint32_t lastAnything = 0;
static uint32_t lastButtonEdge = 0;
static bool buttonWasDown = false;
static bool announced = false;

// ------------------------------------------------------- transport, per board

#if defined(BOARD_NRF52)

BLEUart bleuart;
static bool linkUp() { return Bluefruit.connected() && bleuart.notifyEnabled(); }
static void sendLine(const char *line) {
  writeChunked(line);
}
static void startBle() {
  Bluefruit.begin();
  Bluefruit.setName(UNIT_NAME);
  Bluefruit.setTxPower(4);
  bleuart.begin();
  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addService(bleuart);
  Bluefruit.ScanResponse.addName();
  Bluefruit.Advertising.restartOnDisconnect(true);
  Bluefruit.Advertising.start(0);
}

#else   // ESP32-C6 / C3 / S3

static NimBLECharacteristic *txChar = nullptr;
static NimBLEServer *server = nullptr;
static bool linkUp() { return server && server->getConnectedCount() > 0; }
static void sendLine(const char *line) {
  writeChunked(line);
}
static void startBle() {
  NimBLEDevice::init(UNIT_NAME);
  server = NimBLEDevice::createServer();
  NimBLEService *svc = server->createService(NUS_SERVICE);
  txChar = svc->createCharacteristic(NUS_TX, NIMBLE_PROPERTY::NOTIFY);
  svc->createCharacteristic(NUS_RX, NIMBLE_PROPERTY::WRITE);
  svc->start();
  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(NUS_SERVICE);
  adv->setName(UNIT_NAME);
  adv->start();
}

#endif

// ---------------------------------------------------------------- sending

// The most a BLE notification carries by default is twenty bytes: an ATT
// payload is the MTU less three, and the default MTU is twenty-three. Anything
// longer is silently cut off at the far end.
//
// That is not a cosmetic limit. "T " plus a seven byte uid is twenty-three
// bytes, and seven byte uids are what NTAG213/215/216 and MIFARE Ultralight
// carry. Sending those whole would deliver a short, wrong uid that matches no
// rigged key and looks exactly like a flaky reader.
//
// So every line is written in chunks and the Pi reassembles on the newline,
// which it already does because a serial link splits wherever it likes too.
//
// Chunking alone is not enough. Fired back to back the notifications overflow
// the stack's queue and one goes missing: a fifty-seven byte fault line came
// through once with exactly twenty characters absent from the middle. Only so
// many notifications fit in a connection event, so each chunk is given a
// moment to drain and a failed write is retried rather than dropped.
static const size_t BLE_CHUNK = 20;
static const uint32_t CHUNK_GAP_MS = 12;
static const uint8_t CHUNK_TRIES = 6;

static bool writeRaw(const uint8_t *data, size_t len);

static void writeChunk(const uint8_t *data, size_t len) {
  for (uint8_t attempt = 0; attempt < CHUNK_TRIES; attempt++) {
    if (writeRaw(data, len)) return;
    delay(CHUNK_GAP_MS);
  }
}

static void writeChunked(const char *line) {
  size_t len = strlen(line);
  size_t sent = 0;
  bool multi = (len + 1) > BLE_CHUNK;
  while (sent < len) {
    size_t n = len - sent;
    if (n > BLE_CHUNK) n = BLE_CHUNK;
    writeChunk((const uint8_t *)line + sent, n);
    sent += n;
    if (multi) delay(CHUNK_GAP_MS);
  }
  writeChunk((const uint8_t *)"\n", 1);
}

// --------------------------------------------------------------------- helpers

// Colon separated uppercase hex, the same shape config.json uses for
// rigged_tags, so neither side has to reformat before comparing.
static void formatUid(const uint8_t *uid, uint8_t len, char *out) {
  // Not named HEX: Arduino's Print.h defines that as a macro for base 16.
  static const char *DIGITS = "0123456789ABCDEF";
  uint8_t o = 0;
  for (uint8_t i = 0; i < len; i++) {
    if (i) out[o++] = ':';
    out[o++] = DIGITS[uid[i] >> 4];
    out[o++] = DIGITS[uid[i] & 0x0F];
  }
  out[o] = '\0';
}

#if defined(BOARD_NRF52)
static bool writeRaw(const uint8_t *data, size_t len) {
  // Returns what it managed to queue, so a short write means the buffer was
  // full and the caller should wait and try again.
  return bleuart.write(data, len) == (int)len;
}
#else
static bool writeRaw(const uint8_t *data, size_t len) {
  if (!txChar) return true;              // nothing connected; nothing to send
  txChar->setValue((uint8_t *)data, len);
  return txChar->notify();
}
#endif

static void say(const char *line) {
  if (linkUp()) sendLine(line);
  lastAnything = millis();
}

// ----------------------------------------------------------------------- setup

void setup() {
  Serial.begin(115200);

#if defined(BOARD_NRF52)
  PIN_BUTTON = pinFor(NRF_BUTTON);
  const uint8_t sda = pinFor(NRF_SDA), scl = pinFor(NRF_SCL);
  if (PIN_BUTTON == 0xFF || sda == 0xFF || scl == 0xFF) {
    Serial.println("This board does not expose one of the pins this needs.");
  }
  Wire.setPins(sda, scl);
#endif

  pinMode(PIN_BUTTON, INPUT_PULLUP);

  startBle();
  Serial.printf("BLE up as \"%s\"\n", UNIT_NAME);

  pinMode(LED_BUILTIN, OUTPUT);
  Wire.begin();
  nfc.begin();
  uint32_t version = nfc.getFirmwareVersion();
  readerOk = version != 0;
  if (!readerOk) {
    Serial.println("PN532 not found. Check the DIP switches are set to I2C.");
  } else {
    Serial.printf("PN532 firmware %d.%d\n", (version >> 16) & 0xFF,
                  (version >> 8) & 0xFF);
    nfc.SAMConfig();
  }
}

// ------------------------------------------------------------------------ loop

void loop() {
  const uint32_t now = millis();

  if (linkUp()) {
    if (!announced) {
      char hello[64];
      snprintf(hello, sizeof(hello), "V %s pn532 ble", UNIT_NAME);
      say(hello);
      announced = true;
    }
  } else {
    announced = false;
  }

  // The button first, and every pass: the tag poll is the part that blocks,
  // and pressing the button is the one thing a guest does that must feel
  // instant.
  const bool down = digitalRead(PIN_BUTTON) == LOW;
  if (down != buttonWasDown && now - lastButtonEdge >= DEBOUNCE_MS) {
    lastButtonEdge = now;
    buttonWasDown = down;
    if (down) say("B");
  }

  // A broken reader blinks and complains, rather than going quiet. The wall
  // can then say the unit is alive but faulty, instead of leaving somebody to
  // work out why tapping a key does nothing.
  if (!readerOk) {
    digitalWrite(LED_BUILTIN, (now / 200) % 2);
    if (now - lastComplaint >= 3000) {
      say("E pn532 not found, check the dip switches are set to i2c");
      lastComplaint = now;
    }
    if (now - lastAnything >= HEARTBEAT_MS) say("H");
    return;
  }

  uint8_t uid[7];
  uint8_t uidLength = 0;
  if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength,
                              READ_TIMEOUT_MS)) {
    if (now - lastTagSent >= TAG_REPEAT_MS) {
      char formatted[3 * 7];
      char text[3 + 3 * 7];
      formatUid(uid, uidLength, formatted);
      snprintf(text, sizeof(text), "T %s", formatted);
      say(text);
      lastTagSent = now;
    }
  }

  if (now - lastAnything >= HEARTBEAT_MS) {
    say("H");
  }
}
