"""
camera_handler.py — Y.A.T.R.A. Camera Module

FLUXO COMPLETO (Visão Sob Demanda):
────────────────────────────────────
  1. Discord/Web pede foto  →  capturar_e_descrever()
  2. Verifica se ESP32-CAM está online (heartbeat < 60s)
  3. Seta capture_requested = TRUE no Supabase
  4. ESP32-CAM detecta via polling (a cada 3s)
  5. ESP32-CAM tira foto, faz PUT no Supabase Storage
  6. ESP32-CAM atualiza last_photo_url + capture_requested = FALSE
  7. Backend detecta a URL nova (polling com timeout de 30s)
  8. Envia imagem para Gemini Flash (gratuito) → obtém descrição
  9. Retorna descrição para o chamador (Discord/Web)

VARIÁVEIS DE AMBIENTE NECESSÁRIAS:
    SUPABASE_URL      — URL do projeto Supabase
    SUPABASE_KEY      — anon key (acesso público)
    GEMINI_API_KEY    — Google AI Studio (gratuito: 15 rpm, 1M tokens/day)
"""

import os
import io
import asyncio
import base64
import aiohttp
from datetime import datetime, timezone
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

SUPABASE_URL      = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")

# Tempo máximo esperando a ESP32-CAM tirar e enviar a foto
CAPTURE_TIMEOUT_S = 30
# Após N segundos sem heartbeat, câmera é considerada offline
HEARTBEAT_TIMEOUT_S = 60
# Polling para aguardar foto (intervalo em segundos)
POLL_INTERVAL_S = 2

# ─────────────────────────────────────────────
#  HEADERS SUPABASE
# ─────────────────────────────────────────────
def _supa_headers(content_type: str = "application/json") -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  content_type,
    }

# ─────────────────────────────────────────────
#  VERIFICAR CÂMERA ONLINE
# ─────────────────────────────────────────────
async def verificar_camera_online() -> bool:
    """
    Retorna True se a ESP32-CAM enviou heartbeat
    nos últimos HEARTBEAT_TIMEOUT_S segundos.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/camera_yatra?id=eq.1&select=last_heartbeat,online"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_supa_headers()) as r:
                data = await r.json()
        if not data:
            return False
        last_hb = data[0].get("last_heartbeat")
        if not last_hb:
            return False
        hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
        delta   = (datetime.now(timezone.utc) - hb_time).total_seconds()
        return delta < HEARTBEAT_TIMEOUT_S
    except Exception as e:
        print(f"⚠️  [camera] verificar_online erro: {e}")
        return False


# ─────────────────────────────────────────────
#  OBTER STATUS COMPLETO
# ─────────────────────────────────────────────
async def status_camera() -> dict:
    """
    Retorna dict com:
        online       : bool
        last_photo_url: str | None
        last_photo_at : str | None
        segundos_offline: int
    """
    if not SUPABASE_URL:
        return {"online": False, "last_photo_url": None, "last_photo_at": None, "segundos_offline": -1}
    try:
        url = f"{SUPABASE_URL}/rest/v1/camera_yatra?id=eq.1"
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_supa_headers()) as r:
                data = await r.json()
        if not data:
            return {"online": False, "last_photo_url": None, "last_photo_at": None, "segundos_offline": -1}

        row     = data[0]
        last_hb = row.get("last_heartbeat")
        seg_off = -1
        if last_hb:
            hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
            seg_off = int((datetime.now(timezone.utc) - hb_time).total_seconds())

        return {
            "online":          seg_off >= 0 and seg_off < HEARTBEAT_TIMEOUT_S,
            "last_photo_url":  row.get("last_photo_url"),
            "last_photo_at":   row.get("last_photo_at"),
            "segundos_offline": seg_off,
        }
    except Exception as e:
        print(f"⚠️  [camera] status erro: {e}")
        return {"online": False, "last_photo_url": None, "last_photo_at": None, "segundos_offline": -1}


# ─────────────────────────────────────────────
#  TRIGGER DE CAPTURA
# ─────────────────────────────────────────────
async def solicitar_foto() -> str | None:
    """
    Seta capture_requested=TRUE no Supabase.
    Faz polling até a ESP32-CAM confirmar o envio (last_photo_url muda
    e capture_requested volta pra FALSE).
    Retorna a URL pública da foto ou None se timeout.
    """
    if not SUPABASE_URL:
        return None

    url_patch = f"{SUPABASE_URL}/rest/v1/camera_yatra?id=eq.1"
    headers   = {**_supa_headers(), "Prefer": "return=minimal"}

    # ── 1. Limpa URL antiga + seta flag ──────────────────────────────
    async with aiohttp.ClientSession() as s:
        await s.patch(url_patch, headers=headers, json={
            "capture_requested": True,
            "last_photo_url":    None,
            "last_photo_at":     None,
        })

    # ── 2. Polling até foto aparecer ─────────────────────────────────
    url_poll = f"{SUPABASE_URL}/rest/v1/camera_yatra?id=eq.1&select=capture_requested,last_photo_url"
    deadline  = asyncio.get_event_loop().time() + CAPTURE_TIMEOUT_S

    async with aiohttp.ClientSession() as s:
        while asyncio.get_event_loop().time() < deadline:
            async with s.get(url_poll, headers=_supa_headers()) as r:
                data = await r.json()

            if data:
                row            = data[0]
                foto_url       = row.get("last_photo_url")
                ainda_pendente = row.get("capture_requested", True)

                # Foto chegou e flag foi limpa pela ESP32-CAM
                if foto_url and not ainda_pendente:
                    return foto_url

            await asyncio.sleep(POLL_INTERVAL_S)

    # Timeout: limpa a flag para não deixar a câmera presa
    async with aiohttp.ClientSession() as s:
        await s.patch(url_patch, headers=headers, json={"capture_requested": False})

    return None


# ─────────────────────────────────────────────
#  DESCRIÇÃO VIA GEMINI FLASH (grátis)
# ─────────────────────────────────────────────
async def descrever_com_gemini(photo_url: str, contexto_extra: str = "") -> str:
    if not GEMINI_API_KEY:
        return "❌ GEMINI_API_KEY não configurada. Adicione no .env!"

    # ── 1. Baixa a imagem do Supabase ─────────────────────────────────
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(photo_url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                image_bytes  = await r.read()
                content_type = r.content_type or "image/jpeg"
    except Exception as e:
        return f"❌ Não consegui baixar a imagem: {e}"

    # ── 2. MAGIA: Converte RAW para JPEG se for a foto do ESP32 ───────
    if len(image_bytes) == 38400:  # Tamanho exato do QQVGA RGB565
        try:
            print("[YATRA] Convertendo imagem RAW (RGB565) para JPEG...")
            img = Image.frombytes('RGB;16', (160, 120), image_bytes, 'raw', 'RGB;16')
            jpeg_buffer = io.BytesIO()
            img.save(jpeg_buffer, format="JPEG", quality=85)
            image_bytes = jpeg_buffer.getvalue()
            content_type = "image/jpeg"
            
            # Opcional: Re-upar pro Supabase pra imagem aparecer no site
            headers = {
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "image/jpeg",
                "x-upsert": "true"
            }
            async with aiohttp.ClientSession() as s:
                await s.put(f"{SUPABASE_URL}/storage/v1/object/yatra-camera/latest.jpg", headers=headers, data=image_bytes)
                
        except Exception as e:
            print(f"[ERRO] Falha ao converter RAW: {e}")

    # ── 3. Envia para o Gemini ────────────────────────────────────────
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "Você está vendo uma foto capturada pela câmera de vigilância da Y.A.T.R.A. "
        "Descreva em português o que vê: pessoas, objetos, ambiente, etc. "
        "Seja detalhada mas direta. Máximo 3 parágrafos."
    )
    if contexto_extra:
        prompt += f"\n\nContexto adicional: {contexto_extra}"

    payload = {
        "contents": [{"parts": [{"inline_data": {"mime_type": content_type, "data": image_b64}}, {"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 512},
    }

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(gemini_url, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as r:
                resp = await r.json()

        candidates = resp.get("candidates", [])
        if not candidates:
            return f"❌ Gemini sem resposta."

        return candidates[0]["content"]["parts"][0]["text"]

    except Exception as e:
        return f"❌ Erro ao chamar Gemini: {e}"


# ─────────────────────────────────────────────
#  PIPELINE COMPLETO — chamado pelo Discord e Web
# ─────────────────────────────────────────────
async def capturar_e_descrever(contexto_extra: str = "") -> dict:
    """
    Pipeline completo:
        1. Verifica câmera online
        2. Dispara captura
        3. Aguarda foto
        4. Descreve com Gemini
        5. Retorna resultado

    Retorna:
        {
            "sucesso": bool,
            "erro":    str | None,
            "foto_url": str | None,
            "descricao": str | None,
        }
    """
    # ── Passo 1: câmera online? ───────────────────────────────────────
    online = await verificar_camera_online()
    if not online:
        return {
            "sucesso":   False,
            "erro":      "📷 ESP32-CAM está offline. Sem sinal de heartbeat nos últimos 60s.",
            "foto_url":  None,
            "descricao": None,
        }

    # ── Passo 2 + 3: dispara + aguarda foto ──────────────────────────
    foto_url = await solicitar_foto()
    if not foto_url:
        return {
            "sucesso":   False,
            "erro":      f"⏱️ Timeout: ESP32-CAM não enviou a foto em {CAPTURE_TIMEOUT_S}s.",
            "foto_url":  None,
            "descricao": None,
        }

    # ── Passo 4: descreve ─────────────────────────────────────────────
    descricao = await descrever_com_gemini(foto_url, contexto_extra)

    return {
        "sucesso":   True,
        "erro":      None,
        "foto_url":  foto_url,
        "descricao": descricao,
    }
