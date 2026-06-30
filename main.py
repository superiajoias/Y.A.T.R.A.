import sys
import threading
import asyncio
import os
import re
import json
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify

# ─────────────────────────────────────────────
# FIX PARA WINDOWS (ProactorEventLoop)
# ─────────────────────────────────────────────
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

# ─────────────────────────────────────────────
# IMPORTA MÓDULO DE LÓGICA (ai_brain como módulo puro)
# NÃO usamos o app Flask interno do ai_brain —
# definimos todas as rotas aqui, neste main.py.
# ─────────────────────────────────────────────
import ai_brain
from discord_bot import bot

# ─────────────────────────────────────────────
# FLASK APP
# ─────────────────────────────────────────────
app = Flask(__name__)


# ── ROTA RAIZ ─────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ── STATUS INICIAL (carregado pelo JS na abertura) ──
@app.route('/status')
def status():
    usuario = ai_brain.obter_ou_criar_usuario("local_web_user", "Denis")
    # O usuário "Denis" é exibido como "Miguel" na interface
    apelido = "Miguel" if usuario.get("nome") == "Denis" else (
        usuario.get("apelido") or usuario.get("nome", "usuário")
    )
    pontos = usuario.get("amizade", 0)

    return jsonify({
        "idade_dias":       ai_brain.calcular_idade(),
        "mensagens_totais": ai_brain.estado_yatra.get("mensagens_totais", 0),
        "humor_atual":      ai_brain.estado_yatra.get("humor_atual", "N"),
        "energia":          ai_brain.estado_yatra.get("energia", 100),
        "curiosidade":      ai_brain.estado_yatra.get("curiosidade", 70),
        "medo":             ai_brain.estado_yatra.get("medo", 10),
        "greeting":         ai_brain.gerar_greeting(usuario),
        "nivel_amizade":    ai_brain.nivel_amizade(pontos),
        "pontos_amizade":   pontos,
        "apelido":          apelido,
    })


# ── SENSORES (pooling a cada 3s pelo JS) ─────
@app.route('/sensores')
def sensores():
    return jsonify(ai_brain.telemetria_atual)


# ── ENVIAR MENSAGEM ───────────────────────────
@app.route('/enviar', methods=['POST'])
def enviar():
    dados   = request.get_json(force=True) or {}
    msg     = dados.get('mensagem', '').strip()
    user_id = dados.get('user_id', 'local_web_user')

    if not msg:
        return jsonify({'resposta': '(mensagem vazia)', 'humor': 'N',
                        'energia': 100, 'curiosidade': 70, 'medo': 10,
                        'sensores': ai_brain.telemetria_atual,
                        'nivel_amizade': 'Desconhecido', 'pontos_amizade': 0,
                        'apelido': 'usuário'}), 400

    usuario    = ai_brain.obter_ou_criar_usuario(user_id, "Denis")
    plataforma = usuario.get("plataforma", "web")

    # 1. Salva mensagem do usuário no Supabase
    ai_brain.salvar_no_supabase(user_id, plataforma, "user", msg)

    # 2. Puxa histórico (já inclui a msg recém salva)
    contexto = ai_brain.puxar_contexto_recente(user_id, limite=15)

    # 3. Monta prompt completo
    historico = (
        [{"role": "system", "content": ai_brain.montar_system_prompt(usuario, user_id)}]
        + contexto
    )

    try:
        # 4. Chama a IA
        response = ai_brain.client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=historico,
            temperature=0.75
        )
        resposta_ia = response.choices[0].message.content

        # 5. Extrai tag de humor e limpa o texto de forma robusta
        # Pega a tag com ou sem colchetes e ignora maiúsculas/minúsculas
        regex_humor = r'\[?HUMOR:\s*([a-zA-Z])\s*\]?'
        
        # Encontra todas as tags geradas na resposta
        tags_encontradas = re.findall(regex_humor, resposta_ia, flags=re.IGNORECASE)
        
        if tags_encontradas:
            # Pega sempre a última tag gerada (caso ela mude de ideia no meio da frase)
            novo_humor = tags_encontradas[-1].upper()
            # Garante que a letra existe no seu sistema, se não, volta pro Neutro
            if novo_humor not in ['N', 'A', 'R', 'T', 'C', 'M', 'X', 'E', 'S']:
                novo_humor = ai_brain.estado_yatra.get("humor_atual", "N")
        else:
            novo_humor = ai_brain.estado_yatra.get("humor_atual", "N")

        # Limpa o texto varrendo qualquer vestígio da tag antes de enviar ao usuário
        resposta_clean = re.sub(regex_humor, '', resposta_ia, flags=re.IGNORECASE).strip()

        # 5b. Extrai e salva novos interesses marcados com [GOSTO: item]
        resposta_clean = ai_brain.processar_gostos(user_id, resposta_clean)

        # 6. Atualiza humor no Supabase (para a ESP32 ler)
        try:
            ai_brain.supabase.table("estado_yatra") \
                .update({"humor_atual": novo_humor}) \
                .eq("id", 1).execute()
        except Exception as e:
            print(f"⚠️  Supabase humor update: {e}")

        # 7. Ajusta estados internos (energia/curiosidade/medo) e salva JSON local
        ai_brain.ajustar_estados_internos(novo_humor)

        # 8. Salva resposta da IA no Supabase
        ai_brain.salvar_no_supabase(user_id, plataforma, "assistant", resposta_clean)

        # 9. Atualiza nível de amizade do usuário
        msgs_novo    = usuario.get("mensagens", 0) + 1
        amizade_nova = min(100, usuario.get("amizade", 0) + 1)
        ai_brain.atualizar_usuario(user_id, {"mensagens": msgs_novo, "amizade": amizade_nova})

        # 10. Calcula apelido para exibição
        apelido = "Miguel" if usuario.get("nome") == "Denis" else (
            usuario.get("apelido") or usuario.get("nome", "usuário")
        )

        return jsonify({
            'resposta':       resposta_clean,
            'humor':          novo_humor,
            'energia':        ai_brain.estado_yatra.get("energia", 100),
            'curiosidade':    ai_brain.estado_yatra.get("curiosidade", 70),
            'medo':           ai_brain.estado_yatra.get("medo", 10),
            'sensores':       ai_brain.telemetria_atual,   # ← dict real, não string!
            'nivel_amizade':  ai_brain.nivel_amizade(amizade_nova),
            'pontos_amizade': amizade_nova,
            'apelido':        apelido,
        })

    except Exception as err:
        print(f"❌ Erro na API Groq: {err}")
        apelido_fallback = "Miguel" if usuario.get("nome") == "Denis" else (
            usuario.get("apelido") or usuario.get("nome", "usuário")
        )
        return jsonify({
            'resposta':       '⚠️ Sistema da Yatra teve um erro interno. Tenta de novo!',
            'humor':          'N',
            'energia':        ai_brain.estado_yatra.get("energia", 100),
            'curiosidade':    ai_brain.estado_yatra.get("curiosidade", 70),
            'medo':           ai_brain.estado_yatra.get("medo", 10),
            'sensores':       ai_brain.telemetria_atual,
            'nivel_amizade':  ai_brain.nivel_amizade(usuario.get("amizade", 0)),
            'pontos_amizade': usuario.get("amizade", 0),
            'apelido':        apelido_fallback,
        }), 500


# ── STATUS JSON (leitura rápida do estado_yatra.json) ──
@app.route('/status_json')
def status_json():
    ARQUIVO_ESTADO = "estado_yatra.json"
    emocoes = {
        "N": "Normal 😐",   "R": "Raiva 😡",    "T": "Triste 😢",
        "A": "Alegre ✨",   "C": "Confusa 🤔",   "M": "Medo 😰",
        "X": "Ansiosa 😰",  "E": "Empolgada 🚀", "S": "Sono 😴"
    }
    try:
        with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
            estado = json.load(f)
        estado["humor_texto"] = emocoes.get(estado.get("humor_atual", "N"), "Normal")
        return jsonify(estado)
    except Exception as e:
        print(f"Erro status_json: {e}")
        return jsonify({}), 500


# ── LOGS (endpoint legado, mantido para compatibilidade) ──
@app.route('/logs')
def get_logs():
    humor = ai_brain.estado_yatra.get("humor_atual", "N")
    msgs  = ai_brain.estado_yatra.get("mensagens_totais", 0)
    return f"HUMOR: {humor}\nMENSAGENS: {msgs}\nSENSORES: {json.dumps(ai_brain.telemetria_atual)}"


# ─────────────────────────────────────────────
# RUNNER FLASK (thread separada)
# ─────────────────────────────────────────────
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    print(f"--- [DEBUG] Iniciando Flask na porta {port} ---")
    # host='0.0.0.0' e port=10000 são obrigatórios para o Render
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


# ─────────────────────────────────────────────
# PONTO DE ENTRADA PRINCIPAL
# ─────────────────────────────────────────────
if __name__ == '__main__':
    print("--- [DEBUG] O main.py começou a rodar! ---")

    # Flask em thread separada (daemon → morre junto com o processo principal)
    threading.Thread(target=run_flask, daemon=True).start()

    # Discord Bot ocupa a thread principal (mantém o processo vivo)
    print("--- [DEBUG] Iniciando Discord Bot ---")
    token = os.getenv("DISCORD_TOKEN")
    if token:
        try:
            bot.run(token)
        except Exception as e:
            print(f"❌ O bot parou com o erro: {e}")
    else:
        print("❌ ERRO: DISCORD_TOKEN não encontrado no .env")
        # Se não tiver token (modo só-Flask para testes), mantém Flask vivo
        import time
        while True:
            time.sleep(60)