import asyncio
import websockets
from websockets.server import serve
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
import json
import azure.cognitiveservices.speech as speechsdk
import logging
import os
from aiohttp import web

# Configure logging with timestamps and clean format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Suppress websockets connection rejected logs
logging.getLogger('websockets.server').setLevel(logging.WARNING)

# Azure Configuration - FROM ENVIRONMENT VARIABLES
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY")
AZURE_REGION = os.environ.get("AZURE_REGION", "southeastasia")

# Server Configuration - Render uses PORT environment variable
WS_HOST = "0.0.0.0"
WS_PORT = int(os.environ.get("PORT", 8765))
HTTP_PORT = WS_PORT  # Same port, different protocols

# Audio format from ESP32
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

# Validate Azure credentials on startup
if not AZURE_SPEECH_KEY:
    logger.error("❌ AZURE_SPEECH_KEY environment variable not set!")
    logger.error("   Set it in Render dashboard under Environment tab")
    exit(1)

class AzureSpeechBridge:
    def __init__(self):
        try:
            self.speech_config = speechsdk.SpeechConfig(
                subscription=AZURE_SPEECH_KEY,
                region=AZURE_REGION
            )
            self.speech_config.speech_recognition_language = "en-US"
            logger.info(f"✓ Azure Speech SDK initialized (Region: {AZURE_REGION})")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Azure Speech SDK: {e}")
            raise
        
    async def start_continuous_recognition_aiohttp(self, websocket):
        """Handle continuous real-time speech recognition for aiohttp websocket"""
        try:
            # Create audio stream format
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=SAMPLE_RATE,
                bits_per_sample=16,
                channels=CHANNELS
            )
            
            # Create push stream with proper format
            stream = speechsdk.audio.PushAudioInputStream(stream_format=audio_format)
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )
            
            # Handle session events
            def on_session_started(evt):
                logger.info("🎤 Azure recognition session STARTED")
            
            def on_session_stopped(evt):
                logger.warning("🛑 Azure recognition session STOPPED")
            
            # Handle recognized speech (final results)
            def on_recognized(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    text = evt.result.text
                    if text.strip():
                        logger.info(f"✓ RECOGNIZED: {text}")
                        asyncio.create_task(
                            websocket.send_json({"text": text, "final": True})
                        )
                elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                    logger.warning("⚠️  No speech recognized (NoMatch)")
            
            # Handle partial results
            def on_recognizing(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                    text = evt.result.text
                    if text.strip():
                        logger.info(f"→ Recognizing: {text}")
                        asyncio.create_task(
                            websocket.send_json({"text": text, "final": False})
                        )
            
            # Handle errors
            def on_canceled(evt):
                if evt.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"❌ Recognition error: {evt.error_details}")
            
            # Connect all event handlers
            recognizer.session_started.connect(on_session_started)
            recognizer.session_stopped.connect(on_session_stopped)
            recognizer.recognized.connect(on_recognized)
            recognizer.recognizing.connect(on_recognizing)
            recognizer.canceled.connect(on_canceled)
            
            # Start continuous recognition
            recognizer.start_continuous_recognition()
            logger.info("✓ Continuous recognition started")
            
            return recognizer, stream
            
        except Exception as e:
            logger.error(f"❌ Recognition setup error: {e}", exc_info=True)
            return None, None
    
        """Handle continuous real-time speech recognition"""
        try:
            # Create audio stream format
            audio_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=SAMPLE_RATE,
                bits_per_sample=16,
                channels=CHANNELS
            )
            
            # Create push stream with proper format
            stream = speechsdk.audio.PushAudioInputStream(audio_format)
            audio_config = speechsdk.audio.AudioConfig(stream=stream)
            
            recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )
            
            # Get the event loop
            loop = asyncio.get_running_loop()
            
            # Handle session events
            def on_session_started(evt):
                logger.info("🎤 Azure recognition session STARTED")
            
            def on_session_stopped(evt):
                logger.warning("🛑 Azure recognition session STOPPED")
            
            # Handle recognized speech (final results)
            def on_recognized(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    text = evt.result.text
                    if text.strip():
                        logger.info(f"✓ RECOGNIZED: {text}")
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({"text": text, "final": True})),
                            loop
                        )
                elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                    logger.warning("⚠️  No speech recognized (NoMatch)")
            
            # Handle partial results
            def on_recognizing(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                    text = evt.result.text
                    if text.strip():
                        logger.info(f"→ Recognizing: {text}")
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({"text": text, "final": False})),
                            loop
                        )
            
            # Handle errors
            def on_canceled(evt):
                if evt.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"❌ Recognition error: {evt.error_details}")
            
            # Connect all event handlers
            recognizer.session_started.connect(on_session_started)
            recognizer.session_stopped.connect(on_session_stopped)
            recognizer.recognized.connect(on_recognized)
            recognizer.recognizing.connect(on_recognizing)
            recognizer.canceled.connect(on_canceled)
            
            # Start continuous recognition
            recognizer.start_continuous_recognition()
            logger.info("✓ Continuous recognition started")
            
            return recognizer, stream
            
        except Exception as e:
            logger.error(f"❌ Recognition setup error: {e}", exc_info=True)
            return None, None
    
    def text_to_speech(self, text: str) -> bytes:
        """Convert text to speech audio"""
        try:
            synthesizer = speechsdk.SpeechSynthesizer(
                speech_config=self.speech_config,
                audio_config=None
            )
            result = synthesizer.speak_text_async(text).get()
            
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.info(f"✓ TTS completed: {len(result.audio_data)} bytes")
                return result.audio_data
            else:
                logger.error(f"❌ TTS failed: {result.reason}")
            return None
            
        except Exception as e:
            logger.error(f"❌ TTS Error: {e}")
            return None

bridge = AzureSpeechBridge()

# ============================================
# HTTP Server (for health checks and bots)
# ============================================

async def http_health(request):
    """Health check endpoint - no logging to avoid spam"""
    return web.Response(text="OK\n", status=200)

async def http_root(request):
    """Root endpoint with server info"""
    info = {
        "status": "running",
        "service": "Azure Speech WebSocket Server",
        "websocket_url": "wss://[this-domain]/ws",
        "region": AZURE_REGION,
        "endpoints": {
            "health": "/health",
            "websocket": "/ws"
        }
    }
    return web.json_response(info)

async def start_http_server():
    """Start HTTP server for health checks"""
    app = web.Application()
    
    # Add routes
    app.router.add_get('/health', http_health)
    app.router.add_get('/', http_root)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Start on same port as WebSocket
    site = web.TCPSite(runner, WS_HOST, HTTP_PORT)
    await site.start()
    
    logger.info(f"✓ HTTP server ready on port {HTTP_PORT}")
    logger.info(f"   GET /health - Health check (silent)")
    logger.info(f"   GET /      - Server info")
    
    return runner

# ============================================
# WebSocket Server (for ESP32)
# ============================================

async def handle_client(websocket, path):
    """Handle ESP32 WebSocket connection"""
    client_ip = websocket.remote_address[0]
    logger.info(f"{'='*70}")
    logger.info(f"🔌 ESP32 CONNECTED from {client_ip}")
    logger.info(f"{'='*70}")
    
    recognizer = None
    stream = None
    
    try:
        # Start continuous recognition
        recognizer, stream = await bridge.start_continuous_recognition(websocket)
        
        if not recognizer or not stream:
            logger.error("❌ Failed to start recognition")
            return
        
        # Track stats
        total_bytes = 0
        chunk_count = 0
        last_log_time = asyncio.get_event_loop().time()
        first_chunk = True
        
        async for message in websocket:
            # Binary = Audio chunks for STT (ESP32 → Azure)
            if isinstance(message, bytes):
                # Debug first chunk
                if first_chunk:
                    logger.info(f"📦 First audio chunk: {len(message)} bytes")
                    logger.info(f"   Sample (hex): {message[:16].hex()}")
                    first_chunk = False
                
                # Feed audio to Azure
                stream.write(message)
                total_bytes += len(message)
                chunk_count += 1
                
                # Log stats every 2 seconds
                current_time = asyncio.get_event_loop().time()
                if current_time - last_log_time >= 2.0:
                    elapsed = current_time - last_log_time
                    bytes_per_sec = total_bytes / elapsed if elapsed > 0 else 0
                    duration_sec = total_bytes / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
                    
                    logger.info(f"📊 Audio: {chunk_count} chunks | {total_bytes} bytes | "
                              f"{bytes_per_sec:.0f} B/s | {duration_sec:.1f}s")
                    
                    # Warn if low data rate
                    expected_rate = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
                    if bytes_per_sec < expected_rate * 0.5:
                        logger.warning(f"⚠️  Low data rate! Expected ~{expected_rate} B/s")
                    
                    total_bytes = 0
                    chunk_count = 0
                    last_log_time = current_time
            
            # JSON = Text for TTS
            else:
                try:
                    data = json.loads(message)
                    text = data.get("text", "")
                    
                    if text:
                        logger.info(f"📤 TTS Request: '{text}'")
                        audio = bridge.text_to_speech(text)
                        if audio:
                            await websocket.send(audio)
                            logger.info(f"📥 TTS audio sent: {len(audio)} bytes")
                except json.JSONDecodeError:
                    logger.error(f"❌ Invalid JSON received")
                    
    except ConnectionClosedOK:
        logger.info(f"✓ ESP32 disconnected cleanly: {client_ip}")
    except ConnectionClosedError as e:
        logger.warning(f"⚠️  Connection closed with error: {client_ip}")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
    finally:
        # Cleanup
        if recognizer:
            try:
                recognizer.stop_continuous_recognition()
            except:
                pass
        if stream:
            try:
                stream.close()
            except:
                pass
        logger.info(f"🔒 Connection closed: {client_ip}")

# ============================================
# Combined Server (HTTP + WebSocket on same port)
# ============================================

async def handle_websocket_upgrade(request):
    """Handle WebSocket upgrade requests"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    
    client_ip = request.remote
    logger.info(f"{'='*70}")
    logger.info(f"🔌 ESP32 CONNECTED from {client_ip}")
    logger.info(f"{'='*70}")
    
    recognizer = None
    stream = None
    
    try:
        # Start continuous recognition
        recognizer, stream = await bridge.start_continuous_recognition_aiohttp(ws)
        
        if not recognizer or not stream:
            logger.error("❌ Failed to start recognition")
            return ws
        
        # Track stats
        total_bytes = 0
        chunk_count = 0
        last_log_time = asyncio.get_event_loop().time()
        first_chunk = True
        
        async for msg in ws:
            if msg.type == web.WSMsgType.BINARY:
                # Audio data
                if first_chunk:
                    logger.info(f"📦 First audio chunk: {len(msg.data)} bytes")
                    logger.info(f"   Sample (hex): {msg.data[:16].hex()}")
                    first_chunk = False
                
                stream.write(msg.data)
                total_bytes += len(msg.data)
                chunk_count += 1
                
                # Log stats every 2 seconds
                current_time = asyncio.get_event_loop().time()
                if current_time - last_log_time >= 2.0:
                    elapsed = current_time - last_log_time
                    bytes_per_sec = total_bytes / elapsed if elapsed > 0 else 0
                    duration_sec = total_bytes / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
                    
                    logger.info(f"📊 Audio: {chunk_count} chunks | {total_bytes} bytes | "
                              f"{bytes_per_sec:.0f} B/s | {duration_sec:.1f}s")
                    
                    expected_rate = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
                    if bytes_per_sec < expected_rate * 0.5:
                        logger.warning(f"⚠️  Low data rate! Expected ~{expected_rate} B/s")
                    
                    total_bytes = 0
                    chunk_count = 0
                    last_log_time = current_time
                    
            elif msg.type == web.WSMsgType.TEXT:
                # TTS request
                try:
                    data = json.loads(msg.data)
                    text = data.get("text", "")
                    
                    if text:
                        logger.info(f"📤 TTS Request: '{text}'")
                        audio = bridge.text_to_speech(text)
                        if audio:
                            await ws.send_bytes(audio)
                            logger.info(f"📥 TTS audio sent: {len(audio)} bytes")
                except json.JSONDecodeError:
                    logger.error(f"❌ Invalid JSON received")
                    
            elif msg.type == web.WSMsgType.ERROR:
                logger.error(f"❌ WebSocket error: {ws.exception()}")
                
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}", exc_info=True)
    finally:
        # Cleanup
        if recognizer:
            try:
                recognizer.stop_continuous_recognition()
            except:
                pass
        if stream:
            try:
                stream.close()
            except:
                pass
        logger.info(f"🔒 Connection closed: {client_ip}")
    
    return ws

# ============================================
# Main Server
# ============================================

async def main():
    logger.info("=" * 70)
    logger.info("🚀 Azure Speech WebSocket Server")
    logger.info("=" * 70)
    logger.info(f"   Port: {WS_PORT}")
    logger.info(f"   Azure Region: {AZURE_REGION}")
    logger.info(f"   Audio: {SAMPLE_RATE}Hz, {CHANNELS}ch, 16-bit")
    logger.info(f"   Expected Rate: {SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH} B/s")
    logger.info(f"   Environment: {'Render' if os.environ.get('RENDER') else 'Local'}")
    logger.info("=" * 70)
    
    # Create app with both HTTP and WebSocket
    app = web.Application()
    app.router.add_get('/health', http_health)
    app.router.add_get('/', http_root)
    app.router.add_get('/ws', handle_websocket_upgrade)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, WS_HOST, WS_PORT)
    await site.start()
    
    logger.info(f"✓ Server ready on port {WS_PORT}")
    logger.info(f"   HTTP: http://localhost:{WS_PORT}/")
    logger.info(f"   WebSocket: ws://localhost:{WS_PORT}/ws")
    logger.info("=" * 70)
    logger.info("✅ Ready for connections")
    logger.info("=" * 70)
    
    try:
        await asyncio.Future()
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
