import discord
import edge_tts
import io

async def tts_speak(voice_client, text):
    """Gera áudio na RAM e envia para o Discord via Pipe."""
    try:
        voice = "pt-BR-FranciscaNeural"
        communicate = edge_tts.Communicate(text, voice)
        
        # Cria um buffer na memória
        audio_buffer = io.BytesIO()
        
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])
        
        # Volta o ponteiro para o início do buffer
        audio_buffer.seek(0)
        
        # Para o áudio atual se necessário
        if voice_client.is_playing():
            voice_client.stop()
            
        # Reproduz usando pipe=True para ler o buffer
        voice_client.play(discord.FFmpegPCMAudio(audio_buffer, pipe=True))
        
    except Exception as e:
        print(f"--- [ERRO] Falha no TTS RAM: {e} ---")