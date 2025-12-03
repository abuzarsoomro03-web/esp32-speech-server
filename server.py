import asyncio
import websockets
import json
import azure.cognitiveservices.speech as speechsdk
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Azure Configuration - ONLY use environment variables
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_REGION = os.getenv("AZURE_REGION", "southeastasia")

# Validate required environment variables
if not AZURE_SPEECH_KEY:
    raise ValueError("AZURE_SPEECH_KEY environment variable is required!")

# Server Configuration - Railway provides PORT env variable
WS_HOST = "0.0.0.0"
WS_PORT = int(os.getenv("PORT", 8765))  # Railway assigns this

# Audio format from ESP32
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

class AzureSpeechBridge:
    def __init__(self):
        self.speech_config = speechsdk.SpeechConfig(
            subscription=AZURE_SPEECH_KEY,
            region=AZURE_REGION
        )
        self.speech_config.speech_recognition_language = "en-US"
        
    async def start_continuous_recognition(self, websocket):
        """Handle continuous real-time speech recognition"""
        try:
            # Create audio stream format - THIS IS CRITICAL
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
                    if text.strip():  # Only send non-empty results
                        logger.info(f"✓ RECOGNIZED: {text}")
                        asyncio.run_coroutine_threadsafe(
                            websocket.send(json.dumps({"text": text, "final": True})),
                            loop
                        )
                elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                    logger.warning("⚠️  No speech recognized (NoMatch)")
                    details = evt.result.no_match_details
                    logger.warning(f"   Reason: {details.reason}")
            
            # Handle partial results (real-time feedback)
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
                    logger.error(f"   Error code: {evt.cancellation_details.error_code}")
            
            # Connect all event handlers
            recognizer.session_started.connect(on_session_started)
            recognizer.session_stopped.connect(on_session_stopped)
            recognizer.recognized.connect(on_recognized)
            recognizer.recognizing.connect(on_recognizing)
            recognizer.canceled.connect(on_canceled)
            
            # Start continuous recognition
            recognizer.start_continuous_recognition()
            logger.info("✓ Continuous recognition started - waiting for audio...")
            
            return recognizer, stream
            
        except Exception as e:
            logger.error(f"Recognition setup error: {e}", exc_info=True)
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
                logger.error(f"TTS failed: {result.reason}")
            return None
            
        except Exception as e:
            logger.error(f"TTS Error: {e}")
            return None

bridge = AzureSpeechBridge()

async def handle_client(websocket):
    """Handle ESP32 connection with bidirectional communication"""
    client_ip = websocket.remote_address[0]
    logger.info(f"═══ ESP32 connected: {client_ip} ═══")
    
    recognizer = None
    stream = None
    
    try:
        # Start continuous recognition
        recognizer, stream = await bridge.start_continuous_recognition(websocket)
        
        if not recognizer or not stream:
            logger.error("Failed to start recognition")
            return
        
        # Track stats
        total_bytes = 0
        chunk_count = 0
        last_log_time = asyncio.get_event_loop().time()
        first_chunk = True
        
        async for message in websocket:
            # Binary = Audio chunks for STT (ESP32 → Azure)
            if isinstance(message, bytes):
                # Debug first chunk to verify audio format
                if first_chunk:
                    logger.info(f"📦 First chunk received: {len(message)} bytes")
                    logger.info(f"   Preview (hex): {message[:16].hex()}")
                    logger.info(f"   Preview (decimal): {list(message[:8])}")
                    first_chunk = False
                
                # Feed audio chunk to Azure stream
                stream.write(message)
                total_bytes += len(message)
                chunk_count += 1
                
                # Log stats every 2 seconds
                current_time = asyncio.get_event_loop().time()
                if current_time - last_log_time >= 2.0:
                    elapsed = current_time - last_log_time
                    bytes_per_sec = total_bytes / elapsed if elapsed > 0 else 0
                    duration_sec = total_bytes / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
                    
                    logger.info(f"📊 Streaming: {chunk_count} chunks | {total_bytes} bytes | "
                              f"{bytes_per_sec:.0f} B/s | {duration_sec:.1f}s audio")
                    
                    # Check if we're getting expected data rate
                    expected_rate = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 32000 bytes/sec
                    if bytes_per_sec < expected_rate * 0.5:
                        logger.warning(f"⚠️  Low data rate! Expected ~{expected_rate} B/s, got {bytes_per_sec:.0f} B/s")
                    
                    total_bytes = 0
                    chunk_count = 0
                    last_log_time = current_time
            
            # JSON = Text for TTS (ESP32 → Azure → ESP32)
            else:
                try:
                    data = json.loads(message)
                    text = data.get("text", "")
                    
                    if text:
                        logger.info(f"📤 TTS Request: '{text}'")
                        
                        audio = bridge.text_to_speech(text)
                        if audio:
                            await websocket.send(audio)
                            logger.info(f"📥 Sent TTS audio to ESP32: {len(audio)} bytes")
                        else:
                            logger.error("TTS failed to generate audio")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON: {message}")
                    
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"═══ ESP32 disconnected: {client_ip} ═══")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
    finally:
        # Cleanup
        if recognizer:
            try:
                recognizer.stop_continuous_recognition()
                logger.info("Recognition stopped")
            except:
                pass
        if stream:
            try:
                stream.close()
                logger.info("Stream closed")
            except:
                pass

async def main():
    logger.info("═" * 60)
    logger.info(f"🚀 WebSocket Server Starting on Railway.app")
    logger.info(f"   Host: {WS_HOST}:{WS_PORT}")
    logger.info(f"   Azure Region: {AZURE_REGION}")
    logger.info(f"   Audio: {SAMPLE_RATE}Hz, {CHANNELS} channel, 16-bit")
    logger.info(f"   Expected data rate: {SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH} bytes/sec")
    logger.info("═" * 60)
    
    # Railway health check endpoint
    async def health_check(path, request_headers):
        if path == "/health":
            return (200, [("Content-Type", "text/plain")], b"OK")
    
    try:
        async with websockets.serve(
            handle_client, 
            WS_HOST, 
            WS_PORT,
            process_request=health_check,
            ping_interval=20,
            ping_timeout=20
        ):
            logger.info("✅ Server ready to accept connections")
            await asyncio.Future()
    except Exception as e:
        logger.error(f"Server failed to start: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
