#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <MPU6050.h>


// ─────────────────────────────────────────────
//  PINOUT
// ─────────────────────────────────────────────
#define PIN_DHT 4
#define PIN_TRIG 5
#define PIN_ECHO 18
#define PIN_BOTAO 19
#define PIN_SDA 21
#define PIN_SCL 22
#define PIN_RGB_G 13
#define PIN_RGB_R 12
#define PIN_RGB_B 14
#define PIN_BUZZER 26
#define PIN_MIC 32
#define PIN_FOTO 34

#define OLED_W 128
#define OLED_H 64
#define OLED_ADDR 0x3C
#define DHT_TYPE DHT11

// ─────────────────────────────────────────────
//  CREDENCIAIS
// ─────────────────────────────────────────────
#include "config.h"

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
bool estadoAnteriorBotao = HIGH; 
bool tocandoWeezerActive = false;
int notaAtual = 0;
int duracaoNotaAtual = 0;
unsigned long tempoUltimaNota = 0;

unsigned long ultimo_envio = 0;
unsigned long ultimo_pull = 0;
unsigned long ultimo_pisca = 0;

const unsigned long INTERVALO_ENVIO = 7000;
const unsigned long INTERVALO_PULL = 3000;
const unsigned long INTERVALO_PISCA = 3000;
const unsigned long DURACAO_PISCA = 150;

bool piscando = false;

struct Humor {
  char cod;
  const char* face_normal;
  const char* face_pisca;
  uint8_t r, g, b;
};

Humor HUMORES[] = {
  { 'N', "o o", "- -", 70, 100, 130 },
  { 'A', ":D", ":D", 0, 200, 110 },
  { 'R', ">:c", ">:c", 255, 0, 0 },
  { 'T', ":c", ":c", 0, 0, 255 },
  { 'C', "OwO", "OwO", 140, 0, 200 },
  { 'M', ";w;", ";w;", 225, 255, 0 },
  { 'X', "o_o", "o_o", 0, 180, 255 },
  { 'E', "^w^", "^w^", 200, 235, 0 },
  { 'S', "-w-", "-w-", 40, 0, 120 },
};
const int NUM_HUMORES = 9;

// ─────────────────────────────────────────────
//  PROTÓTIPOS E MÚSICAS
// ─────────────────────────────────────────────
void tocarMario();
void tocarRickroll();
void aplicarHumor(char cod);
void desenharOLED(bool pisca);
void conectarWiFi();
void enviarTelemetria();
void puxarHumor();
void setRGB(uint8_t r, uint8_t g, uint8_t b);
void oledBoot();
float lerDistancia();
void beep(int freq, int dur);

#define R_D5 587
#define R_E5 659
#define R_FS5 740
#define R_A5 880
#define R_B5 988

void verificarBotao() {
  static unsigned long tempoUltimaVerificacao = 0;
  // Debounce de 500ms para evitar disparos falsos
  if (millis() - tempoUltimaVerificacao > 500) {
    if (digitalRead(PIN_BOTAO) == LOW) {
      // Sorteia entre Mario (0) ou Rickroll (1)
      if (random(0, 2) == 0) {
        Serial.println("Tocando Mario...");
        tocarMario();
      } else {
        Serial.println("VOCÊ FOI RICKROLLADO!");
        tocarRickroll();
      }
      tempoUltimaVerificacao = millis();
    }
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIG, OUTPUT);
  pinMode(PIN_ECHO, INPUT);
  pinMode(PIN_BOTAO, INPUT_PULLUP);
  delay(500); // Delay de estabilização
  pinMode(PIN_MIC, INPUT);
  pinMode(PIN_FOTO, INPUT);
  pinMode(PIN_RGB_R, OUTPUT);
  pinMode(PIN_RGB_G, OUTPUT);
  pinMode(PIN_RGB_B, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  ledcAttach(PIN_BUZZER, 2000, 8);
  Wire.begin(PIN_SDA, PIN_SCL);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR)) {
    Serial.println("ERRO OLED!");
  } else {
    oledBoot();
  }
  mpu.initialize();
  dht.begin();
  conectarWiFi();
  aplicarHumor('N');
}

void loop() {
  unsigned long agora = millis();
  verificarBotao();
  
  if (Serial.available() > 0) {
    char cmd = toupper((char)Serial.read());
    if (cmd != '\n' && cmd != '\r' && cmd != ' ') aplicarHumor(cmd);
  }
  if (WiFi.status() != WL_CONNECTED) conectarWiFi();
  if (agora - ultimo_envio >= INTERVALO_ENVIO) {
    ultimo_envio = agora;
    enviarTelemetria();
  }
  if (agora - ultimo_pull >= INTERVALO_PULL) {
    ultimo_pull = agora;
    puxarHumor();
  }
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
//  MÚSICAS E FUNÇÕES
// ─────────────────────────────────────────────

void tocarMario() {
  int melody[] = { 659, 659, 0, 659, 0, 523, 659, 0, 784, 0, 392 };
  int notes = 11;
  int duration = 150;
  for (int i = 0; i < notes; i++) {
    ledcWriteTone(PIN_BUZZER, melody[i]);
    delay(duration);
    ledcWriteTone(PIN_BUZZER, 0);
    delay(50);
  }
}

void tocarRickroll() {
  // Notas do Rickroll (incluindo o final descendo corretamente)
  int melodia[] = { 
    R_D5, R_E5, R_FS5, R_E5, R_A5, R_A5, R_FS5, // Never gonna give you up
    R_D5, R_E5, R_FS5, R_E5, R_B5, R_B5, R_E5,   // Never gonna let you down
  };
  setRGB(255, 0, 0);       // LED RGB vermelho puro
  oled.clearDisplay();   // Limpa o display
  oled.display();
  Serial.println("YATRA entrou em modo de hibernação por Rickroll.");
  
  // As durações ajustadas para o ritmo ficar certo
  int duracoes[] = { 
    8, 8, 8, 8, 4, 4, 2, 
    8, 8, 8, 8, 4, 4, 2
  };
  
  int notas = 21; // Agora temos 21 notas no total
  
  for (int i = 0; i < notas; i++) {
    ledcWriteTone(PIN_BUZZER, melodia[i]);
    delay(1000 / duracoes[i]);
    ledcWriteTone(PIN_BUZZER, 0);
    delay(50);
  }
}

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

Humor* getHumor(char cod) {
  for (int i = 0; i < NUM_HUMORES; i++)
    if (HUMORES[i].cod == cod) return &HUMORES[i];
  return &HUMORES[0];
}

void aplicarHumor(char cod) {
  humor_atual = cod;
  piscando = false;
  ultimo_pisca = millis();
  Humor* h = getHumor(cod);
  setRGB(h->r, h->g, h->b);
  tocarNota(cod);
  desenharOLED(false);
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
  oled.setCursor(20, 20);
  oled.println("Y.A.T.R.A.");
  oled.setCursor(28, 34);
  oled.println("iniciando...");
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
  oled.setCursor(0, 0);
  oled.print("YATRA [");
  oled.print(humor_atual);
  oled.print("]");
  oled.setTextSize(2);
  int16_t cx = (OLED_W - (strlen(face) * 12)) / 2;
  oled.setCursor(cx < 0 ? 0 : cx, 22);
  oled.print(face);
  oled.drawLine(0, 45, 127, 45, SSD1306_WHITE);
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
  }
  if (digitalRead(PIN_MIC) == HIGH) {
    oled.setCursor(90, 50);
    oled.print("SOM!");
  }
  oled.display();
}

void conectarWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 20) {
    delay(500);
    t++;
  }
}

void enviarTelemetria() {
  if (WiFi.status() != WL_CONNECTED) return;
  float temp = dht.readTemperature();
  float umid = dht.readHumidity();
  float dist = lerDistancia();
  int lux = map(analogRead(PIN_FOTO), 0, 4095, 0, 100);
  bool som = (digitalRead(PIN_MIC) == HIGH);
  int16_t ax_r, ay_r, az_r, gx_r, gy_r, gz_r;
  mpu.getMotion6(&ax_r, &ay_r, &az_r, &gx_r, &gy_r, &gz_r);
  float ax = ax_r / 16384.0f, ay = ay_r / 16384.0f, az = az_r / 16384.0f;
  float gx = gx_r / 131.0f, gy = gy_r / 131.0f, gz = gz_r / 131.0f;
  String payload = "{\"temp\":" + String(temp, 1) + ",\"umid\":" + String(umid, 1) + ",\"dist\":" + String((int)dist) + ",\"lux\":" + String(lux) + ",\"som\":" + (som ? "true" : "false") + ",\"ax\":" + String(ax, 3) + ",\"ay\":" + String(ay, 3) + ",\"az\":" + String(az, 3) + ",\"gx\":" + String(gx, 3) + ",\"gy\":" + String(gy, 3) + ",\"gz\":" + String(gz, 3) + "}";
  HTTPClient http;
  http.begin(String(SUPABASE_URL) + "/rest/v1/telemetria_yatra?id=eq.1");
  http.addHeader("Content-Type", "application/json");
  http.addHeader("apikey", SUPABASE_KEY);
  http.addHeader("Authorization", String("Bearer ") + SUPABASE_KEY);
  http.addHeader("Prefer", "return=minimal");
  int code = http.sendRequest("PATCH", payload);
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
      if (novo.length() > 0 && (char)toupper(novo[0]) != humor_atual)
        aplicarHumor(toupper(novo[0]));
    }
  }
  http.end();
}