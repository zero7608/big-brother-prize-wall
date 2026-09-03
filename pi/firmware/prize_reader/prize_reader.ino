// Wireless tag reader and button for the Big Brother memory wall.
//
// An ESP32 with a PN532 and a momentary switch, sending both to the Pi over
// Bluetooth Classic serial.  The Pi pairs once, binds the unit to a serial
// device, and reads lines from it; nothing else about the show changes.
//
// ESP8266 will not do.  It has no Bluetooth of any kind, only WiFi.  This
// needs an ESP32 (any of the classic ones: WROOM-32, DevKitC, and so on).
// NimBLE-only chips such as the ESP32-C3 and S3 have no Bluetooth Classic
// either, so no serial profile: use a WROOM-32 or switch the transport.
//
// ---------------------------------------------------------------- the protocol
//
//   V <text>       once on connect, identifying the unit
//   T <uid>        a tag is on the reader, REPEATED while it stays there
//   B              the button was pressed
//   H              heartbeat, once a second when nothing else is happening
//
// The repetition of T is the part that matters, and it is worth knowing why
// before anyone "optimises" it into a single event per tag.  A key sits on the
// reader for the whole turn.  The wall decides a key has been taken off the pad
// by not having seen it for a few seconds, and that is what stops one guest
// getting a second turn while their key lies there.  Send T once and the wall
// will hand out extra prizes: the symptom looks nothing like a protocol change.
//
// ------------------------------------------------------------------- the wiring
//
//   PN532 (SPI mode: DIP switch 1 off, 2 on)      ESP32
//     SCK                                           GPIO 18
//     MISO                                          GPIO 19
//     MOSI                                          GPIO 23
//     SS / NSS                                      GPIO  5
//     VCC                                           3V3
//     GND                                           GND
//
//   Button: one leg to GPIO 4, the other to GND.  No resistor: the internal
//   pull-up is enabled, so the pin idles high and reads low when pressed.
//
// Needs the Adafruit PN532 library (Library Manager: "Adafruit PN532").

#include <Adafruit_PN532.h>
#include <BluetoothSerial.h>
#include <SPI.h>

// ------------------------------------------------------------------- settings

static const char *UNIT_NAME = "PrizeShack";   // what the Pi will see when pairing
static const uint8_t PIN_SS = 5;
static const uint8_t PIN_BUTTON = 4;

// How often the tag is re-reported while it sits on the reader.  Fast enough
// that the wall never mistakes a resting key for a removed one, slow enough to
// leave the link mostly idle.
static const uint32_t TAG_REPEAT_MS = 150;

// How long the PN532 is allowed to wait for a tag on each poll.  Short, so the
// button stays responsive: this loop is not threaded.
static const uint16_t READ_TIMEOUT_MS = 60;

// A press shorter than this is contact bounce rather than a person.
static const uint32_t DEBOUNCE_MS = 40;

// Silence on the link is ambiguous, so fill it.
static const uint32_t HEARTBEAT_MS = 1000;

// ---------------------------------------------------------------------- state

Adafruit_PN532 nfc(PIN_SS);
BluetoothSerial bt;

static uint32_t lastTagSent = 0;
static uint32_t lastAnything = 0;
static uint32_t lastButtonEdge = 0;
static bool buttonWasDown = false;
static bool announced = false;

// --------------------------------------------------------------------- helpers

// The Pi expects colon separated uppercase hex, the same shape config.json
// uses for rigged_tags, so the two can be compared without either side
// reformatting.
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

static void say(const char *line) {
  bt.println(line);
  lastAnything = millis();
}

// ----------------------------------------------------------------------- setup

void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);

  bt.begin(UNIT_NAME);
  Serial.printf("Bluetooth up as \"%s\"\n", UNIT_NAME);

  nfc.begin();
  uint32_t version = nfc.getFirmwareVersion();
  if (!version) {
    // Nothing useful can happen without the reader, and a silent unit is worse
    // than an obviously broken one: blink the built in LED and say so on USB.
    Serial.println("PN532 not found. Check the DIP switches are set to SPI.");
    pinMode(LED_BUILTIN, OUTPUT);
    while (true) {
      digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
      delay(200);
    }
  }
  Serial.printf("PN532 firmware %d.%d\n", (version >> 16) & 0xFF,
                (version >> 8) & 0xFF);
  nfc.SAMConfig();
}

// ------------------------------------------------------------------------ loop

void loop() {
  const uint32_t now = millis();

  // A fresh connection gets told what it is talking to.  Cleared on drop, so a
  // reconnect identifies itself again.
  if (bt.hasClient()) {
    if (!announced) {
      char hello[64];
      snprintf(hello, sizeof(hello), "V %s pn532 esp32", UNIT_NAME);
      say(hello);
      announced = true;
    }
  } else {
    announced = false;
  }

  // -- the button ------------------------------------------------------------
  // Read every pass, and before the tag poll, because the tag poll is what
  // blocks.  Pressing the button is the one thing a guest does that must feel
  // instant.
  const bool down = digitalRead(PIN_BUTTON) == LOW;
  if (down != buttonWasDown && now - lastButtonEdge >= DEBOUNCE_MS) {
    lastButtonEdge = now;
    buttonWasDown = down;
    if (down) say("B");
  }

  // -- the tag ---------------------------------------------------------------
  uint8_t uid[7];
  uint8_t uidLength = 0;
  if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &uidLength,
                              READ_TIMEOUT_MS)) {
    if (now - lastTagSent >= TAG_REPEAT_MS) {
      char text[3 + 3 * 7];
      char formatted[3 * 7];
      formatUid(uid, uidLength, formatted);
      snprintf(text, sizeof(text), "T %s", formatted);
      say(text);
      lastTagSent = now;
    }
  }

  // -- the heartbeat ---------------------------------------------------------
  if (now - lastAnything >= HEARTBEAT_MS) {
    say("H");
  }
}
