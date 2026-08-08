#include "portal.h"

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

#include "config_store.h"

namespace observer {
namespace {

DNSServer dns;
WebServer server(80);

constexpr uint16_t kDnsPort = 53;
const IPAddress kApIp(192, 168, 4, 1);

String escape(const std::string& s) {
  String out;
  for (char c : s) {
    switch (c) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      default: out += c;
    }
  }
  return out;
}

String checkedIf(bool v) { return v ? String(" checked") : String(); }

String page(const Settings& s) {
  String h;
  h.reserve(6000);
  h += F("<!doctype html><html><head><meta charset=utf-8>"
         "<meta name=viewport content='width=device-width,initial-scale=1'>"
         "<title>Inside observer setup</title><style>"
         "body{font:16px/1.5 system-ui,sans-serif;margin:0;padding:20px;"
         "background:#0f1310;color:#e8e4d9;max-width:640px}"
         "h1{font-size:19px;letter-spacing:.16em;text-transform:uppercase;"
         "color:#8fb08f;font-weight:600}"
         "h2{font-size:14px;letter-spacing:.12em;text-transform:uppercase;"
         "color:#7c8a7c;margin:28px 0 8px;border-top:1px solid #263026;"
         "padding-top:14px}"
         "label{display:block;margin:12px 0 4px;color:#b9c4b6;font-size:14px}"
         "input[type=text],input[type=password],input[type=number],select"
         "{width:100%;padding:10px;border-radius:6px;border:1px solid #2f3b2f;"
         "background:#161c17;color:#e8e4d9;font-size:16px;box-sizing:border-box}"
         "p.note{color:#7c8a7c;font-size:13px;margin:6px 0 0}"
         "button{margin-top:24px;width:100%;padding:14px;border:0;"
         "border-radius:8px;background:#3c6b45;color:#fff;font-size:17px}"
         "</style></head><body><h1>Live in the garden</h1>");

  h += F("<form method=POST action='/save'>");

  h += F("<h2>WiFi</h2>");
  h += F("<label>Network name (SSID)</label><input type=text name=ssid "
         "autocapitalize=none autocorrect=off required>");
  h += F("<label>Password</label><input type=password name=pass "
         "autocapitalize=none autocorrect=off>");
  h += F("<p class=note>Credentials are stored on the display only, in the "
         "ESP32's own WiFi storage. They are never sent anywhere and never "
         "written to the settings this firmware keeps.</p>");

  h += F("<h2>Station</h2>");
  h += F("<label>Host or IP address</label><input type=text name=host value='");
  h += escape(s.stationHost);
  h += F("'>");
  h += F("<label>Port</label><input type=number name=port min=1 max=65535 value=");
  h += String(s.stationPort);
  h += F(">");
  h += F("<label>Poll interval (seconds)</label>"
         "<input type=number name=poll min=5 max=600 value=");
  h += String(s.pollSeconds);
  h += F(">");

  h += F("<h2>Display</h2>");
  h += F("<label>Naming sensitivity threshold</label>"
         "<input type=number name=thresh step=0.01 min=0.05 max=0.99 value=");
  h += String(s.scoreThreshold, 2);
  h += F(">");
  h += F("<p class=note>This filters which detections are named on the screen. "
         "It is <b>not</b> a probability and it is never shown on the display. "
         "A BirdNET score is a model output, not a calibrated confidence that "
         "the identification is correct, and showing it as a percentage would "
         "misrepresent it. Bat passes ignore this setting entirely: "
         "<code>ultrasonic-pass-v1</code> detects passes, not species, so a "
         "pass is reported as a pass with its peak frequency and is never "
         "given a species name.</p>");
  h += F("<label><input type=checkbox name=bats value=1");
  h += checkedIf(s.showBats);
  h += F("> Show bat passes</label>");
  h += F("<label><input type=checkbox name=h24 value=1");
  h += checkedIf(s.use24hClock);
  h += F("> 24-hour clock</label>");
  h += F("<label>Brightness (%)</label>"
         "<input type=number name=bright min=5 max=100 value=");
  h += String(s.brightnessPercent);
  h += F(">");
  h += F("<label>Fallback UTC offset (minutes)</label>"
         "<input type=number name=tzmin min=-720 max=840 value=");
  h += String(s.fallbackUtcOffsetMinutes);
  h += F(">");
  h += F("<p class=note>Only used until the station reports its own local "
         "midnight, which it does on every successful poll.</p>");

  h += F("<h2>Touch orientation</h2>");
  h += F("<p class=note>Change these only if a tap lands in the wrong place.</p>");
  h += F("<label><input type=checkbox name=tswap value=1");
  h += checkedIf(s.touchSwapXY);
  h += F("> Swap X and Y</label>");
  h += F("<label><input type=checkbox name=tflipx value=1");
  h += checkedIf(s.touchFlipX);
  h += F("> Flip X</label>");
  h += F("<label><input type=checkbox name=tflipy value=1");
  h += checkedIf(s.touchFlipY);
  h += F("> Flip Y</label>");

  h += F("<h2>MQTT (not connected yet)</h2>");
  h += F("<p class=note>The station's MQTT publisher is still being built. "
         "These settings are stored now so they survive the firmware update "
         "that switches the feed over to it.</p>");
  h += F("<label><input type=checkbox name=mqon value=1");
  h += checkedIf(s.mqtt.enabled);
  h += F("> Prefer MQTT when available</label>");
  h += F("<label>Broker host</label><input type=text name=mqhost value='");
  h += escape(s.mqtt.host);
  h += F("'>");
  h += F("<label>Broker port</label>"
         "<input type=number name=mqport min=1 max=65535 value=");
  h += String(s.mqtt.port);
  h += F(">");
  h += F("<label>Username</label><input type=text name=mquser value='");
  h += escape(s.mqtt.username);
  h += F("'>");
  h += F("<label>Password</label><input type=password name=mqpass value=''>");
  h += F("<p class=note>Leave the broker password blank to keep the stored "
         "one.</p>");
  h += F("<label>Topic prefix</label><input type=text name=mqpfx value='");
  h += escape(s.mqtt.topicPrefix);
  h += F("'>");

  h += F("<button type=submit>Save and restart</button></form></body></html>");
  return h;
}

std::string argStr(const char* name, const std::string& fallback) {
  if (!server.hasArg(name)) {
    return fallback;
  }
  const String v = server.arg(name);
  return v.length() ? std::string(v.c_str()) : fallback;
}

long argNum(const char* name, long fallback) {
  if (!server.hasArg(name) || server.arg(name).length() == 0) {
    return fallback;
  }
  return server.arg(name).toInt();
}

double argDouble(const char* name, double fallback) {
  if (!server.hasArg(name) || server.arg(name).length() == 0) {
    return fallback;
  }
  return server.arg(name).toDouble();
}

// An unchecked checkbox is simply absent from the POST body, so presence is
// the value.
bool argFlag(const char* name) { return server.hasArg(name); }

// Shared between the handlers, which have to be plain functions.
Settings* g_settings = nullptr;
bool* g_submitted = nullptr;

void handleRoot() { server.send(200, "text/html; charset=utf-8", page(*g_settings)); }

// Every platform's connectivity probe gets redirected to the form, which is
// what makes the phone pop the page up by itself.
void handleCaptive() {
  server.sendHeader("Location", "http://192.168.4.1/", true);
  server.send(302, "text/plain", "");
}

void handleSave() {
  Settings& s = *g_settings;

  s.stationHost = argStr("host", s.stationHost);
  s.stationPort = static_cast<uint16_t>(argNum("port", s.stationPort));
  s.pollSeconds = static_cast<uint16_t>(argNum("poll", s.pollSeconds));
  s.scoreThreshold = argDouble("thresh", s.scoreThreshold);
  s.showBats = argFlag("bats");
  s.use24hClock = argFlag("h24");
  s.brightnessPercent =
      static_cast<uint8_t>(argNum("bright", s.brightnessPercent));
  s.fallbackUtcOffsetMinutes =
      static_cast<int16_t>(argNum("tzmin", s.fallbackUtcOffsetMinutes));
  s.touchSwapXY = argFlag("tswap");
  s.touchFlipX = argFlag("tflipx");
  s.touchFlipY = argFlag("tflipy");

  s.mqtt.enabled = argFlag("mqon");
  s.mqtt.host = argStr("mqhost", s.mqtt.host);
  s.mqtt.port = static_cast<uint16_t>(argNum("mqport", s.mqtt.port));
  s.mqtt.username = argStr("mquser", s.mqtt.username);
  s.mqtt.password = argStr("mqpass", s.mqtt.password);  // blank keeps the old
  s.mqtt.topicPrefix = argStr("mqpfx", s.mqtt.topicPrefix);

  clampSettings(s);
  saveSettings(s);
  markConfigured();

  const String ssid = server.arg("ssid");
  bool wifiGiven = ssid.length() > 0;
  if (wifiGiven) {
    // Hand the credentials straight to the WiFi stack, which persists them in
    // its own NVS namespace. They are not copied anywhere else, and the
    // password is never logged.
    WiFi.persistent(true);
    WiFi.begin(ssid.c_str(), server.arg("pass").c_str());
    Serial.printf("[portal] WiFi credentials accepted for SSID of %u chars\n",
                  static_cast<unsigned>(ssid.length()));
  } else {
    Serial.println("[portal] settings saved; WiFi left unchanged");
  }

  server.send(200, "text/html; charset=utf-8",
              F("<!doctype html><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<body style='font:16px system-ui;background:#0f1310;color:#e8e4d9;"
                "padding:24px'><h1 style='color:#8fb08f;font-size:19px'>Saved</h1>"
                "<p>The display is restarting. This network will disappear.</p>"));
  if (g_submitted != nullptr) {
    *g_submitted = true;
  }
}

}  // namespace

std::string Portal::begin(Settings& settings) {
  settings_ = &settings;
  submitted_ = false;

  WiFi.mode(WIFI_AP_STA);
  WiFi.softAPConfig(kApIp, kApIp, IPAddress(255, 255, 255, 0));
  // Open network, matching the stock firmware's behaviour. It exists only long
  // enough to type a password into and is torn down on reboot.
  WiFi.softAP(Settings::kProvisioningApSsid);
  delay(200);

  dns.setErrorReplyCode(DNSReplyCode::NoError);
  dns.start(kDnsPort, "*", kApIp);

  g_settings = &settings;
  g_submitted = &submitted_;

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  // Connectivity checks used by iOS, Android, Windows and Firefox.
  server.on("/hotspot-detect.html", HTTP_GET, handleCaptive);
  server.on("/generate_204", HTTP_GET, handleCaptive);
  server.on("/gen_204", HTTP_GET, handleCaptive);
  server.on("/ncsi.txt", HTTP_GET, handleCaptive);
  server.on("/connecttest.txt", HTTP_GET, handleCaptive);
  server.on("/canonical.html", HTTP_GET, handleCaptive);
  server.on("/success.txt", HTTP_GET, handleCaptive);
  server.onNotFound(handleCaptive);
  server.begin();

  running_ = true;
  Serial.printf("[portal] AP \"%s\" up at %s\n",
                Settings::kProvisioningApSsid,
                WiFi.softAPIP().toString().c_str());
  return std::string(WiFi.softAPIP().toString().c_str());
}

void Portal::handle() {
  if (!running_) {
    return;
  }
  dns.processNextRequest();
  server.handleClient();
}

void Portal::end() {
  if (!running_) {
    return;
  }
  server.stop();
  dns.stop();
  WiFi.softAPdisconnect(true);
  running_ = false;
}

}  // namespace observer
