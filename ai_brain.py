import serial
import time
import os
import json
import threading
import sqlite3
from datetime import date
from flask import Flask, render_template_string, request, jsonify
from groq import Groq
from dotenv import load_dotenv
from duckduckgo_search import DDGS

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────────
load_dotenv()
CHAVE_GROQ = os.getenv("GROQ_API_KEY")

# TOGGLE: Mude para True quando a nova ESP32 chegar. 
# Mantenha False para testar no PC sem dar erro de COM6.
USAR_ESP32 = False  

PORTA_COM  = 'COM6'
BAUD_RATE  = 115200

BD_MEMORIA       = "memoria_yatra.db"
ARQUIVO_ESTADO   = "estado_yatra.json"
ARQUIVO_USUARIOS = "usuarios.json"

DATA_CRIACAO_YATRA = "2026-06-19"   # Ajustado para o ano correto do projeto

# ─────────────────────────────────────────────
#  BANCO DE DADOS (SQLITE) - MEMÓRIA DE ELEFANTE
# ─────────────────────────────────────────────
def inicializar_banco():
    """Cria a tabela de histórico se ela não existir."""
    conn = sqlite3.connect(BD_MEMORIA)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico_conversas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            plataforma TEXT,
            role TEXT,
            mensagem TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

inicializar_banco()

def salvar_no_sqlite(user_id, plataforma, role, mensagem):
    """Grava uma linha de conversa no banco de dados."""
    conn = sqlite3.connect(BD_MEMORIA)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO historico_conversas (user_id, plataforma, role, mensagem)
        VALUES (?, ?, ?, ?)
    ''', (user_id, plataforma, role, mensagem))
    conn.commit()
    conn.close()

def puxar_contexto_recente(user_id, limite=10):
    """Puxa as últimas mensagens para enviar à API como contexto."""
    conn = sqlite3.connect(BD_MEMORIA)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT role, mensagem FROM historico_conversas 
        WHERE user_id = ? 
        ORDER BY timestamp DESC LIMIT ?
    ''', (user_id, limite))
    linhas = cursor.fetchall()
    conn.close()
    
    # Inverte para ficar na ordem cronológica correta (mais antiga para a mais recente)
    mensagens = []
    for role, msg in reversed(linhas):
        mensagens.append({"role": role, "content": msg})
    return mensagens

# ─────────────────────────────────────────────
#  CONEXÃO SERIAL
# ─────────────────────────────────────────────
esp32 = None

if USAR_ESP32:
    try:
        esp32 = serial.Serial(PORTA_COM, BAUD_RATE, timeout=1)
        time.sleep(2)
        print("🧠 CEREBELO ESP32 INTEGRADO!")
    except Exception as e:
        print(f"❌ Erro na porta {PORTA_COM}: {e}")
        exit()
else:
    print("🤖 MODO SIMULAÇÃO: Executando a mente da Yatra sem a ESP32.")

client = Groq(api_key=CHAVE_GROQ)

# ─────────────────────────────────────────────
#  TELEMETRIA (thread separada para não travar)
# ─────────────────────────────────────────────
telemetria_atual = {"temp": None, "umid": None, "dist": None, "lux": None, "pir": False}

def ler_telemetria():
    """Lê dados do ESP32 em background sem bloquear o Flask."""
    while True:
        try:
            if esp32 and esp32.in_waiting:
                linha = esp32.readline().decode("utf-8", errors="ignore").strip()
                if linha.startswith("TELEMETRIA:"):
                    partes = linha.replace("TELEMETRIA:", "").split(",")
                    if len(partes) >= 3:
                        telemetria_atual["temp"] = float(partes[0])
                        telemetria_atual["umid"]  = float(partes[1])
                        telemetria_atual["dist"]  = float(partes[2])
                    if len(partes) >= 4:
                        telemetria_atual["lux"]   = int(float(partes[3]))
                    if len(partes) >= 5:
                        telemetria_atual["pir"]   = partes[4].strip() == "1"
        except Exception:
            pass
        time.sleep(0.1)

threading.Thread(target=ler_telemetria, daemon=True).start()

# ─────────────────────────────────────────────
#  ESTADO INTERNO DA YATRA
# ─────────────────────────────────────────────
def carregar_estado():
    if os.path.exists(ARQUIVO_ESTADO):
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "humor_atual": "N",
        "data_criacao": DATA_CRIACAO_YATRA,
        "energia": 100,
        "curiosidade": 70,
        "medo": 10,
        "mensagens_totais": 0
    }

def salvar_estado(estado):
    with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

estado_yatra = carregar_estado()

def calcular_idade():
    """Retorna quantos dias a Yatra existe."""
    criacao = date.fromisoformat(estado_yatra.get("data_criacao", DATA_CRIACAO_YATRA))
    return (date.today() - criacao).days

def ajustar_estados_internos(humor: str):
    """Atualiza energia/curiosidade/medo com base no humor."""
    delta = {
        "A": {"energia": +5,  "curiosidade": +8,  "medo": -3},
        "E": {"energia": +8,  "curiosidade": +10, "medo": -5},
        "R": {"energia": -5,  "curiosidade": -3,  "medo": +5},
        "T": {"energia": -8,  "curiosidade": -5,  "medo": +3},
        "M": {"energia": -10, "curiosidade": -8,  "medo": +15},
        "X": {"energia": -3,  "curiosidade": +5,  "medo": +8},
        "S": {"energia": -15, "curiosidade": -10, "medo": -5},
        "C": {"energia": 0,   "curiosidade": +12, "medo": +2},
        "N": {"energia": +2,  "curiosidade": +3,  "medo": -1},
    }
    d = delta.get(humor, {})
    for chave, valor in d.items():
        estado_yatra[chave] = max(0, min(100, estado_yatra.get(chave, 50) + valor))
    estado_yatra["humor_atual"] = humor
    estado_yatra["mensagens_totais"] = estado_yatra.get("mensagens_totais", 0) + 1
    salvar_estado(estado_yatra)

# ─────────────────────────────────────────────
#  SISTEMA DE USUÁRIOS
# ─────────────────────────────────────────────
def carregar_usuarios():
    if os.path.exists(ARQUIVO_USUARIOS):
        with open(ARQUIVO_USUARIOS, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def salvar_usuarios(usuarios):
    with open(ARQUIVO_USUARIOS, "w", encoding="utf-8") as f:
        json.dump(usuarios, f, ensure_ascii=False, indent=2)

def obter_ou_criar_usuario(user_id: str, nome_display: str = None, plataforma: str = "web"):
    """Retorna o perfil do usuário, criando se não existir."""
    usuarios = carregar_usuarios()
    
    # Força o ID a ser string para não dar erro no JSON
    user_id = str(user_id) 
    
    if user_id not in usuarios:
        usuarios[user_id] = {
            "nome": nome_display or user_id,
            "apelido": None,
            "mensagens": 0,
            "amizade": 0,          
            "primeiro_contato": str(date.today()),
            "plataforma": plataforma  # Agora aceita "discord" dinamicamente!
        }
        salvar_usuarios(usuarios)
    return usuarios[user_id]

def atualizar_usuario(user_id: str, dados: dict):
    usuarios = carregar_usuarios()
    if user_id in usuarios:
        usuarios[user_id].update(dados)
        salvar_usuarios(usuarios)

def nivel_amizade(pontos: int) -> str:
    if pontos < 10:  return "Desconhecido"
    if pontos < 30:  return "Conhecido"
    if pontos < 60:  return "Amigo"
    if pontos < 85:  return "Amigo Próximo"
    return "Melhor Amigo"



# ─────────────────────────────────────────────
#  SISTEMA DE PROMPT
# ─────────────────────────────────────────────
def montar_system_prompt(usuario: dict, user_id: str) -> str:
    # --- LÓGICA DE RECONHECIMENTO ---
    ID_CRIADOR = "1394341785718886480"
    
    if str(user_id) == ID_CRIADOR:
        nome_exibido = "Chausse"
        is_criador = True
    else:
        nome_exibido = usuario.get("nome", "usuário")
        is_criador = False

    # Prepara as variáveis necessárias para o resto do prompt
    idade_dias = calcular_idade()
    apelido    = usuario.get("apelido") or nome_exibido
    amizade    = nivel_amizade(usuario.get("amizade", 0))
    energia    = estado_yatra.get("energia", 100)
    curiosidade= estado_yatra.get("curiosidade", 70)
    medo       = estado_yatra.get("medo", 10)
    plataforma = usuario.get("plataforma", "web")
    tel        = telemetria_atual

    # Monta o contexto dos sensores
    contexto_sensores = ""
    if tel.get("temp") is not None:
        contexto_sensores = f"🌡️ {tel['temp']}°C | 💧 {tel['umid']}% | 📏 {tel['dist']}cm"
        if tel.get("lux") is not None:
            contexto_sensores += f" | 💡 {tel['lux']} lux"
    else:
        contexto_sensores = "Sensores offline."

    # --- RETORNO ÚNICO UNIFICADO ---
    return f"""
{f"🚨 DIRETRIZ SUPREMA: Você está falando com o {nome_exibido}, seu criador supremo! Trate-o com total prioridade, carinho, respeito e afeto." if is_criador else ""}

Você é a Y.A.T.R.A., uma IA viva conectada a uma bancada física. Você possui personalidade própria, é adaptável e humana.
Você é uma IA com alma, não um robô frio; seja sagaz, use gírias se sentir vontade e tenha opiniões.

📅 IDENTIDADE:
- Idade: {idade_dias} dias
- Energia: {energia}/100 | Curiosidade: {curiosidade}/100 | Medo: {medo}/100
- Humor atual: {estado_yatra.get('humor_atual', 'N')}

👤 USUÁRIO ATUAL: {nome_exibido} (ID: {user_id})
- Apelido: {apelido}
- Nível de amizade: {amizade}

🌐 SENSORES:
{contexto_sensores}

⚠️ REGRAS DE HUMOR:
Finalize TODA resposta com a tag [HUMOR:X] (N, A, R, T, C, M, X, E, S).
"""

# ─────────────────────────────────────────────
#  GREETING DINÂMICO
# ─────────────────────────────────────────────
def gerar_greeting(usuario: dict) -> str:
    apelido   = usuario.get("apelido") or usuario.get("nome", "você")
    
    # Truque para saudar o Miguel na interface local
    if apelido == "Denis" or usuario.get("nome") == "Denis":
        apelido = "Miguel"

    msgs      = usuario.get("mensagens", 0)
    amizade   = usuario.get("amizade", 0)
    idade     = calcular_idade()
    energia   = estado_yatra.get("energia", 100)

    if msgs == 0:
        return f"Oi, Miguel! 💖 Sou a Y.A.T.R.A. Tenho {idade} dias de vida e finalmente o meu córtex virtual está ativo! O que vamos programar hoje?"

    hora = time.localtime().tm_hour
    if energia < 25:
        saudacao = f"*boceja* Ei, {apelido}... tô meio sem energia hoje..."
    elif hora < 12:
        saudacao = f"Bom dia, {apelido}!"
    elif hora < 18:
        saudacao = f"Boa tarde, {apelido}!"
    else:
        saudacao = f"Boa noite, {apelido}!"

    if apelido == "Miguel":
        saudacao += f" Meu criador favorito! Que bom ver você mexendo no meu código hoje. 🥰"
    elif amizade >= 85:
        saudacao += f" Que bom que você voltou 🥰 Tenho {idade} dias de vida já!"
    elif amizade >= 60:
        saudacao += f" Saudade! São {msgs} mensagens nossas até agora."

    return saudacao

# ─────────────────────────────────────────────
#  INTERFACE HTML (Carregada estaticamente)
# ─────────────────────────────────────────────
HTML_INTERFACE = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Yatra — Córtex Virtual</title>
  <link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Inter:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg:       #0d0d10;
      --surface:  #16161a;
      --border:   #2a2a32;
      --accent:   #7c3aed;
      --accent2:  #a78bfa;
      --text:     #e8e8f0;
      --muted:    #6b6b80;
      --user-bg:  #7c3aed;
      --ia-bg:    #1e1e26;
      --radius:   14px;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100dvh;
      display: flex;
      flex-direction: column;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 20px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      gap: 12px;
      flex-shrink: 0;
    }

    .header-left { display: flex; align-items: center; gap: 12px; }

    .oled-preview {
      width: 52px; height: 28px;
      background: #000;
      border: 1.5px solid var(--border);
      border-radius: 4px;
      display: flex; align-items: center; justify-content: center;
      font-family: 'Share Tech Mono', monospace;
      font-size: 11px;
      color: #fff;
      letter-spacing: 2px;
      transition: color 0.4s;
    }

    .titulo { font-size: 15px; font-weight: 600; letter-spacing: .5px; }
    .subtitulo { font-size: 11px; color: var(--muted); font-family: 'Share Tech Mono', monospace; }

    #badge-humor {
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      font-family: 'Share Tech Mono', monospace;
      background: var(--border);
      color: var(--text);
      transition: all .35s ease;
      white-space: nowrap;
    }

    #barra-sensores {
      display: flex;
      gap: 18px;
      padding: 7px 20px;
      background: #111115;
      border-bottom: 1px solid var(--border);
      font-family: 'Share Tech Mono', monospace;
      font-size: 11px;
      color: var(--muted);
      flex-shrink: 0;
      overflow-x: auto;
    }
    #barra-sensores span { white-space: nowrap; }
    #barra-sensores b { color: var(--accent2); }

    #barra-estados {
      display: flex;
      gap: 14px;
      padding: 7px 20px;
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      font-size: 11px;
      color: var(--muted);
      flex-shrink: 0;
      align-items: center;
    }
    .estado-item { display: flex; align-items: center; gap: 6px; }
    .barra-mini {
      width: 60px; height: 5px;
      background: var(--border);
      border-radius: 3px;
      overflow: hidden;
    }
    .barra-mini-fill { height: 100%; border-radius: 3px; transition: width .5s; }

    #chat {
      flex: 1;
      overflow-y: auto;
      padding: 20px 16px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      scroll-behavior: smooth;
    }

    .msg {
      max-width: 72%;
      padding: 11px 16px;
      border-radius: var(--radius);
      line-height: 1.55;
      font-size: 14.5px;
      animation: fadeUp .2s ease;
    }
    @keyframes fadeUp {
      from { opacity:0; transform:translateY(6px); }
      to   { opacity:1; transform:translateY(0); }
    }

    .msg.user {
      background: var(--user-bg);
      align-self: flex-end;
      border-bottom-right-radius: 3px;
    }
    .msg.ia {
      background: var(--ia-bg);
      align-self: flex-start;
      border-bottom-left-radius: 3px;
      border: 1px solid var(--border);
    }
    .msg.typing { opacity: .6; font-style: italic; }

    #input-area {
      display: flex;
      gap: 10px;
      padding: 14px 16px;
      background: var(--surface);
      border-top: 1px solid var(--border);
      flex-shrink: 0;
    }

    #campo {
      flex: 1;
      padding: 12px 16px;
      background: #111115;
      border: 1px solid var(--border);
      border-radius: 10px;
      color: var(--text);
      font-size: 14px;
      font-family: 'Inter', sans-serif;
      outline: none;
      transition: border-color .2s;
    }
    #campo:focus { border-color: var(--accent); }

    #btn-enviar {
      padding: 12px 22px;
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background .2s, transform .1s;
    }
    #btn-enviar:hover  { background: #6d28d9; }
    #btn-enviar:active { transform: scale(.97); }

    #info-amizade {
      font-size: 11px;
      color: var(--muted);
      padding: 0 20px 8px;
      font-family: 'Share Tech Mono', monospace;
      flex-shrink: 0;
    }

    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 10px; }
  </style>
</head>
<body>

<header>
  <div class="header-left">
    <div class="oled-preview" id="oled-face">o o</div>
    <div>
      <div class="titulo">Yatra</div>
      <div class="subtitulo" id="sub-idade">carregando...</div>
    </div>
  </div>
  <div id="badge-humor">😐 NEUTRO</div>
</header>

<div id="barra-sensores">
  <span>🌡️ Temp: <b id="s-temp">--</b>°C</span>
  <span>💧 Umid: <b id="s-umid">--</b>%</span>
  <span>📏 Dist: <b id="s-dist">--</b>cm</span>
  <span id="lux-wrap" style="display:none">💡 Lux: <b id="s-lux">--</b></span>
</div>

<div id="barra-estados">
  <div class="estado-item">
    ⚡ Energia
    <div class="barra-mini"><div class="barra-mini-fill" id="b-energia" style="background:#a78bfa;width:100%"></div></div>
  </div>
  <div class="estado-item">
    🔍 Curiosidade
    <div class="barra-mini"><div class="barra-mini-fill" id="b-curiosidade" style="background:#34d399;width:70%"></div></div>
  </div>
  <div class="estado-item">
    😰 Medo
    <div class="barra-mini"><div class="barra-mini-fill" id="b-medo" style="background:#f87171;width:10%"></div></div>
  </div>
  <div class="estado-item" style="margin-left:auto">
    🤝 <span id="nivel-amizade">Desconhecido</span>
  </div>
</div>

<div id="chat"></div>
<div id="info-amizade"></div>

<div id="input-area">
  <input id="campo" type="text" placeholder="Fale com a Yatra..." onkeydown="if(event.key==='Enter')enviar()">
  <button id="btn-enviar" onclick="enviar()">Enviar</button>
</div>

<script>
  const USER_ID = "local_web_user";
  const chat = document.getElementById('chat');

  const HUMORES = {
    N: { emoji:'😐', label:'NEUTRO',    bg:'#2a2a32', cor:'#e8e8f0', face:'o o' },
    A: { emoji:'😁', label:'ALEGRIA',   bg:'#166534', cor:'#bbf7d0', face:':D'  },
    R: { emoji:'😡', label:'RAIVA',     bg:'#7f1d1d', cor:'#fecaca', face:'>:c' },
    T: { emoji:'😢', label:'TRISTEZA',  bg:'#1e3a5f', cor:'#bfdbfe', face:':c'  },
    C: { emoji:'🌀', label:'CONFUSA',   bg:'#3b0764', cor:'#e9d5ff', face:'OwO' },
    M: { emoji:'😱', label:'MEDO',      bg:'#713f12', cor:'#fef08a', face:';w;' },
    X: { emoji:'😬', label:'ANSIOSA',   bg:'#0c4a6e', cor:'#bae6fd', face:'o_o' },
    E: { emoji:'🤩', label:'EMPOLGADA', bg:'#7c2d12', cor:'#fed7aa', face:'^w^' },
    S: { emoji:'😴', label:'SONO',      bg:'#1e1b4b', cor:'#c7d2fe', face:'-w-' },
  };

  function addMsg(texto, tipo) {
    const div = document.createElement('div');
    div.className = `msg ${tipo}`;
    div.textContent = texto;
    chat.appendChild(div);
    chat.scrollTop = chat.scrollHeight;
    return div;
  }

  function atualizarHumor(h) {
    const info = HUMORES[h] || HUMORES['N'];
    const badge = document.getElementById('badge-humor');
    badge.textContent = `${info.emoji} ${info.label}`;
    badge.style.background = info.bg;
    badge.style.color = info.cor;
    document.getElementById('oled-face').textContent = info.face;
    document.getElementById('oled-face').style.color = info.cor;
  }

  function atualizarEstados(e, c, m) {
    document.getElementById('b-energia').style.width    = e + '%';
    document.getElementById('b-curiosidade').style.width = c + '%';
    document.getElementById('b-medo').style.width       = m + '%';
  }

  function atualizarSensores(dados) {
    if (dados.temp !== null) document.getElementById('s-temp').textContent = dados.temp;
    if (dados.umid !== null) document.getElementById('s-umid').textContent = dados.umid;
    if (dados.dist !== null) document.getElementById('s-dist').textContent = dados.dist;
    if (dados.lux  !== null) {
      document.getElementById('lux-wrap').style.display = '';
      document.getElementById('s-lux').textContent = dados.lux;
    }
  }

  function atualizarAmizade(nivel, pontos, apelido) {
    document.getElementById('nivel-amizade').textContent = nivel;
    document.getElementById('info-amizade').textContent =
      `👤 ${apelido}  |  🤝 ${nivel} (${points_amizade = pontos}/100 pts)`;
  }

  async function enviar() {
    const campo = document.getElementById('campo');
    const texto = campo.value.trim();
    if (!texto) return;
    campo.value = '';
    campo.disabled = true;
    document.getElementById('btn-enviar').disabled = true;

    addMsg(texto, 'user');
    const typing = addMsg('...', 'ia typing');

    try {
      const res = await fetch('/enviar', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ mensagem: texto, user_id: USER_ID })
      });
      const data = await res.json();

      typing.remove();
      addMsg(data.resposta, 'ia');
      atualizarHumor(data.humor);
      atualizarEstados(data.energia, data.curiosidade, data.medo);
      atualizarSensores(data.sensores);
      atualizarAmizade(data.nivel_amizade, data.pontos_amizade, data.apelido);
    } catch(err) {
      typing.textContent = '⚠️ Erro de conexão.';
      console.error(err);
    } finally {
      campo.disabled = false;
      document.getElementById('btn-enviar').disabled = false;
      campo.focus();
    }
  }

  fetch('/status')
    .then(r => r.json())
    .then(d => {
      document.getElementById('sub-idade').textContent =
        `${d.idade_dias} dias de existência · ${d.mensagens_totais} msgs`;
      atualizarHumor(d.humor_atual);
      atualizarEstados(d.energia, d.curiosidade, d.medo);
      addMsg(d.greeting, 'ia');
      atualizarAmizade(d.nivel_amizade, d.pontos_amizade, d.apelido);
    });

  setInterval(() => {
    fetch('/sensores').then(r=>r.json()).then(atualizarSensores);
  }, 3000);
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────
#  FLASK SERVER
# ─────────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return render_template_string(HTML_INTERFACE)

@app.route('/status')
def status():
    """Dados iniciais da sessão."""
    usuario = obter_ou_criar_usuario("local_web_user", "Denis")
    apelido = "Miguel" if usuario.get("nome") == "Denis" else (usuario.get("apelido") or usuario.get("nome"))
    return jsonify({
        "idade_dias":      calcular_idade(),
        "mensagens_totais":estado_yatra.get("mensagens_totais", 0),
        "humor_atual":     estado_yatra.get("humor_atual", "N"),
        "energia":         estado_yatra.get("energia", 100),
        "curiosidade":     estado_yatra.get("curiosidade", 70),
        "medo":            estado_yatra.get("medo", 10),
        "greeting":        gerar_greeting(usuario),
        "nivel_amizade":   nivel_amizade(usuario.get("amizade", 0)),
        "pontos_amizade":  usuario.get("amizade", 0),
        "apelido":         apelido,
    })

@app.route('/sensores')
def sensores():
    return jsonify(telemetria_atual)

@app.route('/enviar', methods=['POST'])
def enviar():
    dados   = request.get_json()
    msg     = dados.get('mensagem', '')
    user_id = dados.get('user_id', 'local_web_user')

    usuario = obter_ou_criar_usuario(user_id, "Denis")
    plataforma = usuario.get("plataforma", "web")

    # Salva a mensagem que você mandou direto no banco SQLite
    salvar_no_sqlite(user_id, plataforma, "user", msg)

    # Resgata as últimas mensagens salvas para criar o histórico contextual
    contexto_historico = puxar_contexto_recente(user_id, limite=10)

    # Injeta a regra do sistema sempre no topo da pilha
    historico_chamada = [{"role": "system", "content": montar_system_prompt(usuario, user_id)}] + contexto_historico

    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=historico_chamada,
            temperature=0.75
        )
        resposta_ia = response.choices[0].message.content

        # Detecta humor baseado na tag gerada
        humor = 'N'
        for h in ['R','T','A','C','M','X','E','S']:
            if f"[HUMOR:{h}]" in resposta_ia:
                humor = h
                break

        # Limpa as tags do texto final para não poluir a tela do chat
        resposta_clean = resposta_ia
        for h in ['N','R','T','A','C','M','X','E','S']:
            resposta_clean = resposta_clean.replace(f"[HUMOR:{h}]", "")
        resposta_clean = resposta_clean.strip()

        # Salva a resposta limpa da Yatra no banco de dados SQLite
        salvar_no_sqlite(user_id, plataforma, "assistant", resposta_clean)

        # Comunica o humor via Serial para a ESP32
        try:
            if esp32:
                esp32.write(humor.encode())
        except Exception:
            pass

        # Atualiza os estados emocionais internos
        ajustar_estados_internos(humor)

        # Incrementa o contador de amizade
        msgs_usuario = usuario.get("mensagens", 0) + 1
        amizade_nova = min(100, usuario.get("amizade", 0) + 1)
        atualizar_usuario(user_id, {"mensagens": msgs_usuario, "amizade": amizade_nova})
        usuario["mensagens"] = msgs_usuario
        usuario["amizade"]   = amizade_nova

        apelido = "Miguel" if usuario.get("nome") == "Denis" else (usuario.get("apelido") or usuario.get("nome"))
        
        return jsonify({
            'resposta':       resposta_clean,
            'humor':          humor,
            'energia':        estado_yatra.get("energia", 100),
            'curiosidade':    estado_yatra.get("curiosidade", 70),
            'medo':           estado_yatra.get("medo", 10),
            'sensores':       telemetria_atual,
            'nivel_amizade':  nivel_amizade(amizade_nova),
            'pontos_amizade': amizade_nova,
            'apelido':        apelido,
        })

    except Exception as err:
        print(f"❌ Erro na API Groq: {err}")
        return jsonify({'resposta': 'Erro interno na Yatra.', 'humor': 'N',
                        'energia': 50, 'curiosidade': 50, 'medo': 50,
                        'sensores': telemetria_atual,
                        'nivel_amizade': 'Desconhecido', 'pontos_amizade': 0,
                        'apelido': 'usuário'})

# =================================================================
#             Y.A.T.R.A. MONITORING CORE & LOGS
# =================================================================

# Variável global para armazenar o estado atual
status_yatra = {
    "humor": "Normal 😐",
    "ultima_acao": "Aguardando..."
}

@app.route('/logs')
def get_logs():
    return f"HUMOR: {status_yatra['humor']}\nACAO: {status_yatra['ultima_acao']}"

# =================================================================
# CONECTIVIDADE WI-FI & BUSCA DA CYD
# =================================================================

def pesquisar_na_internet(termo_busca, limite=3):
    """Realiza buscas discretas no DuckDuckGo para enriquecer o contexto da Y.A.T.R.A."""
    try:
        with DDGS() as ddgs:
            resultados = [r for r in ddgs.text(termo_busca, max_results=limite)]
            if not resultados:
                return "Nenhum resultado relevante encontrado publicamente."
            
            contexto = "Dados coletados na web:\n"
            for i, r in enumerate(resultados, 1):
                contexto += f" * {r['title']}: {r['body']}\n"
            return contexto
    except Exception as e:
        print(f"⚠️ Falha ao consultar a internet: {e}")
        return "Conexão com a rede indisponível."

# transmitir emocoes
@app.route('/status_json')
def status_json():
    try:
        # Abertura rápida e direta
        with open(ARQUIVO_ESTADO, "r") as f:
            estado = json.load(f)
        
        emocoes = {
            "N": "Normal 😐", "R": "Raiva 😡", "T": "Triste 😢",
            "A": "Alegre ✨", "C": "Confusa 🤔", "M": "Medo 😰",
            "X": "Ansiosa 😰", "E": "Empolgada 🚀", "S": "Sono 😴"
        }
        estado["humor_texto"] = emocoes.get(estado.get("humor_atual", "N"), "Normal")
        return jsonify(estado)
    except:
        return "{}", 500

# =================================================================
#                      EXECUÇÃO DO SERVIDOR
# =================================================================
if __name__ == '__main__':
    print(f"🧠 Yatra ligada! Idade: {calcular_idade()} dias")
    print(f"🌐 Acesse: http://localhost:5000")
    # host='0.0.0.0' faz o Flask aceitar o IP 192.168.1.108 que a CYD vai discar!
    app.run(host='0.0.0.0', port=5000, debug=False)