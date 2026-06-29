// ═══════════════════════════════════════════════════════════════
//  Y.A.T.R.A. — FIRMWARE ESP32 DevKit V1 (30 pinos)
//  Código Completo e Corrigido
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
const char* WIFI_SSID     = "BIO_Andrey_2G";
const char* WIFI_PASS     = "110722.*";
const char* SUPABASE_URL  = "https://idqozyyryadgydbdpvhq.supabase.co";
const char* SUPABASE_KEY  = "sb_publishable_Wm3Gw7tPm9HaMBic7Ko3Bg_C08eET8v";

bool estadoAnteriorBotao = LOW; 

void loop() {
  unsigned long agora = millis();

  // --- Lógica do Botão (Montagem em Positivo) ---
  int leituraBotao = digitalRead(PIN_BOTAO);
  
  // Se o botão foi apertado (HIGH) e antes estava solto (LOW)
  if (leituraBotao == HIGH && estadoAnteriorBotao == LOW) {
    Serial.println("Botão apertado! Tocando...");
    tocarWeezer();
    delay(50); // Debounce simples
  }
  estadoAnteriorBotao = leituraBotao;


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

#define OLED_W     128
#define OLED_H     64
#define OLED_ADDR  0x3C
#define DHT_TYPE   DHT11

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
unsigned long ultimo_envio     = 0;
unsigned long ultimo_pull      = 0;
unsigned long ultimo_pisca     = 0;
const unsigned long INTERVALO_ENVIO = 10000;
const unsigned long INTERVALO_PULL  = 5000;
const unsigned long INTERVALO_PISCA = 3000;
const unsigned long DURACAO_PISCA   = 150;
bool piscando = false;

struct Humor {
  char  cod;
  const char* face_normal;
  const char* face_pisca;
  uint8_t r, g, b;
};

Humor HUMORES[] = {
  { 'N', "\xBA \xBA", "- -",  70,  70,  70 },
  { 'A', ":D",        ":D",    0, 200,  80 },
  { 'R', ">:c",       ">:c", 255,   0,   0 },
  { 'T', ":c",        ":c",    0,   0, 200 },
  { 'C', "OwO",       "OwO", 140,   0, 200 },
  { 'M', ";w;",       ";w;", 255, 100,   0 },
  { 'X', "o_o",       "o_o",   0, 180, 255 },
  { 'E', "^w^",       "^w^", 255, 200,   0 },
  { 'S', "-w-",       "-w-",  20,   0,  80 },
};
const int NUM_HUMORES = 9;

// Notas do Riff
#define NOTE_A4  440
#define NOTE_B4  494
#define NOTE_C5  523
#define NOTE_CS5 554
#define NOTE_D5  587
int melodia[] = { NOTE_A4, NOTE_B4, NOTE_C5, NOTE_CS5, 0, NOTE_D5, NOTE_CS5, NOTE_B4, NOTE_A4 };
int duracoes[] = { 12, 12, 12, 6, 16, 6, 6, 6, 3 };

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG,   OUTPUT);
  pinMode(PIN_ECHO,   INPUT);
  pinMode(PIN_BOTAO,  INPUT_PULLDOWN);
  pinMode(PIN_MIC,    INPUT);
  pinMode(PIN_FOTO,   INPUT);
  pinMode(PIN_RGB_R,  OUTPUT);
  pinMode(PIN_RGB_G,  OUTPUT);
  pinMode(PIN_RGB_B,  OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  Wire.begin(PIN_SDA, PIN_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("ERRO OLED!");
  } else {
    oledBoot();
  }
  
  mpu.initialize();
  dht.begin();
  conectarWiFi();
  
  beep(440, 80); delay(60); beep(660, 120);
  aplicarHumor('N');
}

// ─────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────
void loop() {
  unsigned long agora = millis();

  // Botão Riff
  if (digitalRead(PIN_BOTAO) == LOW) {
    tocarWeezer();
    delay(500);
  }

  // Serial
  if (Serial.available() > 0) {
    char cmd = toupper((char)Serial.read());
    if (cmd != '\n' && cmd != '\r' && cmd != ' ') aplicarHumor(cmd);
  }

  // Conectividade
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();

  // Telemetria
  if (agora - ultimo_envio >= INTERVALO_ENVIO) {
    ultimo_envio = agora;
    enviarTelemetria();
  }

  // Pull Supabase
  if (agora - ultimo_pull >= INTERVALO_PULL) {
    ultimo_pull = agora;
    puxarHumor();
  }

  // Piscar
  if (humor_atual == 'N') {
    if (!piscando && agora - ultimo_pisca >= INTERVALO_PISCA) {
      piscando = true;
      ultimo_pisca = agora;
      desenharOLED(true);
    }
    if (piscando && agora - ultimo_pisca >= DURACAO_PISCA) {
      piscando = false;
      desenharOLED(false);
    }
  }
}

// ─────────────────────────────────────────────
//  FUNÇÕES
// ─────────────────────────────────────────────

void beep(int freq, int dur) {
  tone(PIN_BUZZER, freq, dur);
  delay(dur + 10);
  noTone(PIN_BUZZER);
}

void tocarNota(char cod) {
  tone(PIN_BUZZER, 880, 50); 
  delay(60); 
  noTone(PIN_BUZZER);
}

void tocarWeezer() {
  int totalNotas = sizeof(melodia) / sizeof(melodia[0]);
  for (int i = 0; i < totalNotas; i++) {
    int duracaoNota = 1200 / duracoes[i];
    if (melodia[i] == 0) noTone(PIN_BUZZER);
    else tone(PIN_BUZZER, melodia[i], duracaoNota);
    delay(duracaoNota * 1.3);
    noTone(PIN_BUZZER);
  }
}

Humor* getHumor(char cod) {
  for (int i = 0; i < NUM_HUMORES; i++)
    if (HUMORES[i].cod == cod) return &HUMORES[i];
  return &HUMORES[0];
}

void aplicarHumor(char cod) {
  humor_atual = cod;
  piscando    = false;
  ultimo_pisca = millis();
  Humor* h = getHumor(cod);
  setRGB(h->r, h->g, h->b);
  tocarNota(cod);
  desenharOLED(false);
  Serial.print("Humor: "); Serial.println(cod);
}

void setRGB(uint8_t r, uint8_t g, uint8_t b) {
  analogWrite(PIN_RGB_R, r);
  analogWrite(PIN_RGB_G, g);
  analogWrite(PIN_RGB_B, b);
}

void oledBoot() {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(20, 20); oled.println("Y.A.T.R.A.");
  oled.setCursor(28, 34); oled.println("iniciando...");
  oled.display();
}

float lerDistancia() {
  digitalWrite(PIN_TRIG, LOW);
  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH);
  delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  long dur = pulseIn(PIN_ECHO, HIGH, 30000);
  return (dur == 0) ? -1.0 : dur * 0.0343 / 2.0;
}

void desenharOLED(bool pisca) {
  Humor* h = getHumor(humor_atual);
  const char* face = pisca ? h->face_pisca : h->face_normal;
  float temp = dht.readTemperature();
  float dist = lerDistancia();
  
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0); oled.print("YATRA ["); oled.print(humor_atual); oled.print("]");
  
  oled.setTextSize(2);
  int16_t cx = (OLED_W - (strlen(face) * 12)) / 2;
  oled.setCursor(cx < 0 ? 0 : cx, 22);
  oled.print(face);
  
  oled.drawLine(0, 45, 127, 45, SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 50);
  if (!isnan(temp)) { oled.print(temp, 1); oled.print("C"); } else { oled.print("--C"); }
  if (dist > 0 && dist < 400) { oled.print("  "); oled.print((int)dist); oled.print("cm"); }
  if (digitalRead(PIN_MIC) == HIGH) { oled.setCursor(90, 50); oled.print("SOM!"); }
  oled.display();
}

void conectarWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 20) { delay(500); t++; }
}

void enviarTelemetria() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(String(SUPABASE_URL) + "/rest/v1/telemetria_yatra");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("apikey", SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
  http.sendRequest("POST", "{\"id\":1}");
  http.end();
}

void puxarHumor() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(String(SUPABASE_URL) + "/rest/v1/estado_yatra?id=eq.1&select=humor_atual");
  http.addHeader("apikey", SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
  if (http.GET() == 200) {
    String body = http.getString();
    StaticJsonDocument<128> doc;
    if (!deserializeJson(doc, body) && doc.size() > 0) {
      String novo = doc[0]["humor_atual"].as<String>();
      if (novo.length() > 0 && (char)toupper(novo[0]) != humor_atual) aplicarHumor(toupper(novo[0]));
    }
  }
  http.end();
}