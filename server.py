import asyncio
import json
import azure.cognitiveservices.speech as speechsdk
import logging
import os
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_REGION = os.environ.get("AZURE_REGION", "southeastasia")

WS_HOST = "0.0.0.0"
WS_PORT = int(os.environ.get("PORT", 8765))

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2

if not AZURE_SPEECH_KEY:
    logger.error("AZURE_SPEECH_KEY not set!")
    exit(1)

class AzureSpeechBridge:
    def __init__(self):
        self.speech_config = speechsdk.SpeechConfig(
            subscription=AZURE_SPEECH_KEY,
            region=AZURE_REGION
        )
        self.speech_config.speech_recognition_language = "en-US"
        logger.info(f"Azure Speech SDK initialized (Region: {AZURE_REGION})")
    
    async def start_continuous_recognition_aiohttp(self, websocket):
        try:
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1
            )
            
            stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )
            
            def on_session_started(evt):
                logger.info("Azure session STARTED")
            
            def on_session_stopped(evt):
                logger.warning("Azure session STOPPED")
            
            def on_recognized(evt):
                logger.info(f"Recognition event: {evt.result.reason}")
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    text = evt.result.text
                    logger.info(f"RECOGNIZED: {text}")
                    asyncio.create_task(
                        websocket.send_json({"text": text, "final": True})
                    )
                elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                    logger.warning("NoMatch - no speech detected")
            
            def on_recognizing(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                    text = evt.result.text
                    logger.info(f"Recognizing: {text}")
                    asyncio.create_task(
                        websocket.send_json({"text": text, "final": False})
                    )
            
            def on_canceled(evt):
                logger.error(f"Canceled: {evt.reason} - {evt.error_details if evt.reason == speechsdk.CancellationReason.Error else ''}")
            
            recognizer.session_started.connect(on_session_started)
            recognizer.session_stopped.connect(on_session_stopped)
            recognizer.recognized.connect(on_recognized)
            recognizer.recognizing.connect(on_recognizing)
            recognizer.canceled.connect(on_canceled)
            
            return recognizer, stream
            
        except Exception as e:
            logger.error(f"Recognition setup error: {e}")
            return None, None
    
    def text_to_speech(self, text: str) -> bytes:
        try:
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=None
            )
            result = synthesizer.speak_text_async(text).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                return result.audio_data
            return None
        except Exception as e:
            logger.error(f"TTS Error: {e}")
            return None

bridge = AzureSpeechBridge()

async def http_health(request):
    return web.Response(text="OK\n", status=200)

async def handle_websocket_upgrade(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_ip = request.remote
    logger.info(f"ESP32 connected from {client_ip}")
    
    recognizer = None
    stream = None
    recognition_started = False
    
    try:
        recognizer, stream = await bridge.start_continuous_recognition_aiohttp(ws)
        
        if not recognizer or not stream:
            logger.error("Failed to setup recognition")
            return ws
        
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                if not recognition_started:
                    logger.info(f"First audio chunk: {len(msg.data)} bytes")
                    recognizer.start_continuous_recognition()
                    recognition_started = True
                    logger.info("Recognition started")
                
                stream.write(msg.data)
                    
            elif msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    text = data.get("text", "")
                    
                    if text:
                        logger.info(f"TTS: {text}")
                        audio = bridge.text_to_speech(text)
                        if audio:
                            await ws.send_bytes(audio)
                except json.JSONDecodeError:
                    pass
                
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if recognizer and recognition_started:
            try:
                recognizer.stop_continuous_recognition()
            except:
                pass
        if stream:
            try:
                stream.close()
            except:
                pass
        logger.info(f"Connection closed: {client_ip}")
    
    return ws

async def main():
    logger.info("Starting Azure Speech Server")
    logger.info(f"Port: {WS_PORT}")
    logger.info(f"Region: {AZURE_REGION}")
    
    app = web.Application()
    app.router.add_get('/health', http_health)
    app.router.add_get('/ws', handle_websocket_upgrade)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, WS_HOST, WS_PORT)
    await site.start()
    
    logger.info(f"Server ready on port {WS_PORT}")
    
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
