// ═══════════════════════════════════════════════════════════════
//  Y.A.T.R.A. — FIRMWARE ESP32 DevKit V1 (30 pinos)
//  Comunicação: HTTP POST → Supabase REST API + Serial debug
// ═══════════════════════════════════════════════════════════════

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <MPU6050.h>

// ─────────────────────────────────────────────
//  CREDENCIAIS
// ─────────────────────────────────────────────
const char* WIFI_SSID    = "SEU_WIFI";
const char* WIFI_PASS    = "SUA_SENHA";
const char* SUPABASE_URL = "https://XXXX.supabase.co";
const char* SUPABASE_KEY = "SEU_ANON_KEY";

// ─────────────────────────────────────────────
//  PINOUT
// ─────────────────────────────────────────────
#define PIN_DHT    4
#define PIN_TRIG   5
#define PIN_ECHO   18
#define PIN_BOTAO  19
#define PIN_SDA    21
#define PIN_SCL    22
#define PIN_RGB_G  13
#define PIN_RGB_R  12
#define PIN_RGB_B  14
#define PIN_BUZZER 26
#define PIN_MIC    32
#define PIN_FOTO   34

#define OLED_W    128
#define OLED_H    64
#define OLED_ADDR 0x3C
#define DHT_TYPE  DHT11

// ─────────────────────────────────────────────
//  OBJETOS
// ─────────────────────────────────────────────
DHT dht(PIN_DHT, DHT_TYPE);
Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);
MPU6050 mpu;

// ─────────────────────────────────────────────
//  ESTADO
// ─────────────────────────────────────────────
char humor_atual = 'N';

unsigned long ultimo_envio    = 0;
unsigned long ultimo_pull     = 0;
unsigned long ultimo_pisca    = 0;
unsigned long ultima_anim_rgb = 0;

const unsigned long INTERVALO_ENVIO = 10000;
const unsigned long INTERVALO_PULL  = 5000;
const unsigned long INTERVALO_PISCA = 3000;  // pisca olhos a cada 3s
const unsigned long DURACAO_PISCA   = 150;   // duração do piscar em ms

bool piscando = false;

// ─────────────────────────────────────────────
//  TABELA DE HUMORES
// ─────────────────────────────────────────────
struct Humor {
  char  cod;
  const char* face_normal;   // face padrão
  const char* face_pisca;    // frame do piscar (só N usa)
  uint8_t r, g, b;
};

// Face Normal: º º  (char ASCII 186 = º)
// Pisca:       - -
Humor HUMORES[] = {
  { 'N', "\xBA \xBA", "- -",  70,  70,  70 },  // Normal    — cinza
  { 'A', ":D",        ":D",    0, 200,  80 },   // Alegre    — verde
  { 'R', ">:c",       ">:c", 255,   0,   0 },   // Raiva     — vermelho
  { 'T', ":c",        ":c",    0,   0, 200 },   // Triste    — azul
  { 'C', "OwO",       "OwO", 140,   0, 200 },   // Confusa   — roxo
  { 'M', ";w;",       ";w;", 255, 100,   0 },   // Medo      — laranja
  { 'X', "o_o",       "o_o",   0, 180, 255 },   // Ansiosa   — ciano
  { 'E', "^w^",       "^w^", 255, 200,   0 },   // Empolgada — amarelo
  { 'S', "-w-",       "-w-",  20,   0,  80 },   // Sono      — índigo
};
const int NUM_HUMORES = 9;

Humor* getHumor(char cod) {
  for (int i = 0; i < NUM_HUMORES; i++)
    if (HUMORES[i].cod == cod) return &HUMORES[i];
  return &HUMORES[0];
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== Y.A.T.R.A. BOOT ===");
  Serial.println("Comandos: N A R T C M X E S");

  pinMode(PIN_TRIG,   OUTPUT);
  pinMode(PIN_ECHO,   INPUT);
  pinMode(PIN_BOTAO,  INPUT_PULLUP);
  pinMode(PIN_MIC,    INPUT);
  pinMode(PIN_FOTO,   INPUT);
  pinMode(PIN_RGB_R,  OUTPUT);
  pinMode(PIN_RGB_G,  OUTPUT);
  pinMode(PIN_RGB_B,  OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  setRGB(0, 0, 0);
  noTone(PIN_BUZZER);

  Wire.begin(PIN_SDA, PIN_SCL);

  // OLED
  if (!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("ERRO: OLED nao encontrado!");
  } else {
    Serial.println("OK: OLED");
    oledBoot();
  }

  // MPU6050
  mpu.initialize();
  Serial.println(mpu.testConnection() ? "OK: MPU6050" : "AVISO: MPU6050 nao encontrado");

  dht.begin();
  Serial.println("OK: DHT11");

  conectarWiFi();

  // Beep de boot
  beep(440, 80); delay(60); beep(660, 120);

  Serial.println("=== PRONTA ===");
  aplicarHumor('N');
}

// ─────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────
void loop() {
  unsigned long agora = millis();

  // ── Lê serial (comando de humor)
  if (Serial.available() > 0) {
    char cmd = (char)Serial.read();
    cmd = toupper(cmd);  // aceita minúsculo também
    // Descarta \n e \r
    if (cmd == '\n' || cmd == '\r' || cmd == ' ') {
      // ignora
    } else {
      bool valido = false;
      const char validos[] = "NARTCMXES";
      for (int i = 0; i < 9; i++) {
        if (cmd == validos[i]) { valido = true; break; }
      }
      if (valido) {
        Serial.print(">> Humor recebido: ");
        Serial.println(cmd);
        aplicarHumor(cmd);
      } else {
        Serial.print("?? Comando desconhecido: ");
        Serial.println(cmd);
        Serial.println("   Use: N A R T C M X E S");
      }
    }
  }

  // ── Reconecta Wi-Fi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi perdido, reconectando...");
    conectarWiFi();
  }

  // ── Envia telemetria
  if (agora - ultimo_envio >= INTERVALO_ENVIO) {
    ultimo_envio = agora;
    enviarTelemetria();
  }

  // ── Puxa humor do Supabase
  if (agora - ultimo_pull >= INTERVALO_PULL) {
    ultimo_pull = agora;
    puxarHumor();
  }

  // ── Piscar olhos (só humor N)
  if (humor_atual == 'N') {
    if (!piscando && agora - ultimo_pisca >= INTERVALO_PISCA) {
      piscando = true;
      ultimo_pisca = agora;
      desenharOLED(true);  // frame "- -"
    }
    if (piscando && agora - ultimo_pisca >= DURACAO_PISCA) {
      piscando = false;
      desenharOLED(false); // volta º º
    }
  }

  delay(30);
}

// ─────────────────────────────────────────────
//  APLICAR HUMOR (muda tudo de uma vez)
// ─────────────────────────────────────────────
void aplicarHumor(char cod) {
  humor_atual = cod;
  piscando    = false;
  ultimo_pisca = millis();

  Humor* h = getHumor(cod);
  setRGB(h->r, h->g, h->b);
  tocarNota(cod);
  desenharOLED(false);

  Serial.print("Humor ativo: ");
  Serial.println(cod);
}

// ─────────────────────────────────────────────
//  OLED
// ─────────────────────────────────────────────
void oledBoot() {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(20, 20);
  oled.println("Y.A.T.R.A.");
  oled.setCursor(28, 34);
  oled.println("iniciando...");
  oled.display();
}

void desenharOLED(bool pisca) {
  Humor* h = getHumor(humor_atual);
  const char* face = pisca ? h->face_pisca : h->face_normal;

  // Sensores rápidos
  float temp = dht.readTemperature();
  float dist = lerDistancia();

  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);

  // ── Header: "YATRA [N]"
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.print("YATRA [");
  oled.print(humor_atual);
  oled.print("]");

  // ── Face centralizada (linha ~28)
  oled.setTextSize(2);
  // Centraliza: cada char tamanho 2 = 12px, string ~3 chars = 36px
  // Centro 128px → offset = (128 - 36) / 2 = 46
  int16_t cx = (OLED_W - (strlen(face) * 12)) / 2;
  if (cx < 0) cx = 0;
  oled.setCursor(cx, 22);
  oled.print(face);

  // ── Linha separadora
  oled.drawLine(0, 45, 127, 45, SSD1306_WHITE);

  // ── Sensores (rodapé)
  oled.setTextSize(1);
  oled.setCursor(0, 50);
  if (!isnan(temp)) {
    oled.print(temp, 1);
    oled.print("C");
  } else {
    oled.print("--C");
  }

  if (dist > 0 && dist < 400) {
    oled.print("  ");
    oled.print((int)dist);
    oled.print("cm");
  } else {
    oled.print("  --cm");
  }

  // Mic
  if (digitalRead(PIN_MIC) == HIGH) {
    oled.setCursor(90, 50);
    oled.print("SOM!");
  }

  oled.display();
}

// ─────────────────────────────────────────────
//  RGB
// ─────────────────────────────────────────────
void setRGB(uint8_t r, uint8_t g, uint8_t b) {
  analogWrite(PIN_RGB_R, r);
  analogWrite(PIN_RGB_G, g);
  analogWrite(PIN_RGB_B, b);
}

// ─────────────────────────────────────────────
//  BUZZER
// ─────────────────────────────────────────────
void beep(int freq, int ms) {
  tone(PIN_BUZZER, freq, ms);
  delay(ms + 10);
  noTone(PIN_BUZZER);
}

void tocarNota(char cod) {
  switch (cod) {
    case 'A': beep(523,80); beep(659,80); beep(784,120); break;
    case 'E': beep(784,60); beep(880,60); beep(988,100); break;
    case 'R': beep(200,200); delay(50); beep(180,300);   break;
    case 'T': beep(330,300); delay(50); beep(294,400);   break;
    case 'M': beep(440,50); beep(466,50); beep(440,50);  break;
    case 'S': beep(262,500);                              break;
    case 'C': beep(523,50); beep(494,50); beep(523,50);  break;
    case 'X': beep(400,80); beep(400,80); beep(400,80);  break;
    case 'N': beep(440,100);                              break;
  }
}

// ─────────────────────────────────────────────
//  SENSORES
// ─────────────────────────────────────────────
float lerDistancia() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  long dur = pulseIn(PIN_ECHO, HIGH, 30000);
  if (dur == 0) return -1.0;
  return dur * 0.0343 / 2.0;
}

// ─────────────────────────────────────────────
//  WI-FI
// ─────────────────────────────────────────────
void conectarWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("Conectando Wi-Fi");
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 30) {
    delay(500); Serial.print("."); t++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWi-Fi OK: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWi-Fi FALHOU (rodando offline)");
  }
}

// ─────────────────────────────────────────────
//  SUPABASE — envia telemetria
// ─────────────────────────────────────────────
void enviarTelemetria() {
  float temp = dht.readTemperature();
  float umid = dht.readHumidity();
  float dist = lerDistancia();
  int   lux  = map(analogRead(PIN_FOTO), 0, 4095, 0, 100);
  bool  som  = digitalRead(PIN_MIC) == HIGH;

  int16_t axR,ayR,azR,gxR,gyR,gzR;
  mpu.getMotion6(&axR,&ayR,&azR,&gxR,&gyR,&gzR);
  float ax=axR/16384.0, ay=ayR/16384.0, az=azR/16384.0;
  float gx=gxR/131.0,   gy=gyR/131.0,   gz=gzR/131.0;

  bool dht_ok = !isnan(temp) && !isnan(umid);

  String body = "{\"id\":1";
  body += ",\"temp\":"  + String(dht_ok ? temp : -1, 1);
  body += ",\"umid\":"  + String(dht_ok ? umid : -1, 1);
  body += ",\"dist\":"  + String(dist > 0 ? dist : -1, 1);
  body += ",\"lux\":"   + String(lux);
  body += ",\"som\":"   + String(som ? "true" : "false");
  body += ",\"ax\":"    + String(ax,3);
  body += ",\"ay\":"    + String(ay,3);
  body += ",\"az\":"    + String(az,3);
  body += ",\"gx\":"    + String(gx,2);
  body += ",\"gy\":"    + String(gy,2);
  body += ",\"gz\":"    + String(gz,2);
  body += "}";

  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.begin(String(SUPABASE_URL) + "/rest/v1/telemetria_yatra");
  http.addHeader("Content-Type",  "application/json");
  http.addHeader("apikey",        SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
  http.addHeader("Prefer",        "resolution=merge-duplicates");
  int code = http.sendRequest("POST", body);
  Serial.println("Telemetria enviada: HTTP " + String(code));
  http.end();
}

// ─────────────────────────────────────────────
//  SUPABASE — puxa humor
// ─────────────────────────────────────────────
void puxarHumor() {
  if (WiFi.status() != WL_CONNECTED) return;

  HTTPClient http;
  http.begin(String(SUPABASE_URL) + "/rest/v1/estado_yatra?id=eq.1&select=humor_atual");
  http.addHeader("apikey",        SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
  int code = http.GET();

  if (code == 200) {
    String body = http.getString();
    StaticJsonDocument<128> doc;
    if (!deserializeJson(doc, body) && doc.is<JsonArray>() && doc.size() > 0) {
      String novo = doc[0]["humor_atual"].as<String>();
      char novoCod = novo.length() > 0 ? (char)toupper(novo[0]) : 'N';
      if (novoCod != humor_atual) {
        Serial.print("Supabase humor: ");
        Serial.println(novoCod);
        aplicarHumor(novoCod);
      }
    }
  }
  http.end();
}
