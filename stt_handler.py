import speech_recognition as sr
import io

recognizer = sr.Recognizer()

def transcrever_audio(audio_bytes):
    """Recebe bytes do Discord e transcreve na RAM."""
    try:
        # Cria um arquivo virtual na memória
        audio_file = io.BytesIO(audio_bytes)
        
        with sr.AudioFile(audio_file) as source:
            audio_data = recognizer.record(source)
            # Transcreve
            return recognizer.recognize_google(audio_data, language="pt-BR")
    except Exception as e:
        print(f"--- [ERRO] Falha na transcrição: {e} ---")
        return None