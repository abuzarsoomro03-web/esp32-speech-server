import asyncio
import websockets
import json
import aiohttp
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Azure Configuration
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY")
AZURE_REGION = os.getenv("AZURE_REGION", "southeastasia")

if not AZURE_SPEECH_KEY:
    raise ValueError("AZURE_SPEECH_KEY environment variable is required!")

# Server Configuration
WS_HOST = "0.0.0.0"
WS_PORT = int(os.getenv("PORT", 8765))

# Audio format from ESP32
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit

class AzureSpeechBridge:
    def __init__(self):
        self.speech_key = AZURE_SPEECH_KEY
        self.region = AZURE_REGION
        
    async def recognize_continuous(self, websocket, audio_buffer):
        """
        Use Azure REST API for speech recognition instead of SDK
        This avoids Railway firewall issues with SDK's internal WebSocket
        """
        url = f"https://{self.region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"
        
        params = {
            "language": "en-US",
            "format": "detailed"
        }
        
        headers = {
            "Ocp-Apim-Subscription-Key": self.speech_key,
            "Content-Type": "audio/wav; codec=audio/pcm; samplerate=16000",
            "Accept": "application/json"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, params=params, headers=headers, data=audio_buffer) as response:
                    if response.status == 200:
                        result = await response.json()
                        
                        # Extract recognized text
                        if "NBest" in result and len(result["NBest"]) > 0:
                            text = result["NBest"][0]["Display"]
                            logger.info(f"✓ RECOGNIZED: {text}")
                            
                            await websocket.send(json.dumps({
                                "text": text,
                                "final": True
                            }))
                            return text
                        else:
                            logger.warning("⚠️  No speech recognized")
                    else:
                        error_text = await response.text()
                        logger.error(f"Azure API error {response.status}: {error_text}")
                        
        except Exception as e:
            logger.error(f"Recognition error: {e}", exc_info=True)
        
        return None
    
    async def text_to_speech(self, text: str) -> bytes:
        """Convert text to speech using REST API"""
        url = f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1"
        
        headers = {
            "Ocp-Apim-Subscription-Key": self.speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-16khz-16bit-mono-pcm",
            "User-Agent": "ESP32-TTS-Client"
        }
        
        ssml = f"""<speak version='1.0' xml:lang='en-US'>
            <voice name='en-US-AriaNeural'>
                {text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}
            </voice>
        </speak>"""
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=ssml) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        logger.info(f"✓ TTS completed: {len(audio_data)} bytes")
                        return audio_data
                    else:
                        error_text = await response.text()
                        logger.error(f"TTS error {response.status}: {error_text}")
        except Exception as e:
            logger.error(f"TTS Error: {e}")
        
        return None

bridge = AzureSpeechBridge()

async def handle_client(websocket):
    """Handle ESP32 connection"""
    client_ip = websocket.remote_address[0]
    logger.info(f"═══ ESP32 connected: {client_ip} ═══")
    
    try:
        # Track stats
        audio_buffer = bytearray()
        chunk_count = 0
        last_recognition_time = asyncio.get_event_loop().time()
        recognition_interval = 3.0  # Recognize every 3 seconds of audio
        
        async for message in websocket:
            # Binary = Audio chunks
            if isinstance(message, bytes):
                audio_buffer.extend(message)
                chunk_count += 1
                
                # Calculate audio duration
                buffer_duration = len(audio_buffer) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH)
                current_time = asyncio.get_event_loop().time()
                
                # Recognize every N seconds of audio
                if buffer_duration >= recognition_interval:
                    logger.info(f"📦 Buffered {buffer_duration:.1f}s ({len(audio_buffer)} bytes)")
                    logger.info("   Sending to Azure REST API...")
                    
                    # Create WAV header for the audio
                    wav_data = create_wav(audio_buffer, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)
                    
                    # Send to Azure
                    await bridge.recognize_continuous(websocket, wav_data)
                    
                    # Clear buffer for next segment
                    audio_buffer.clear()
                    chunk_count = 0
                    last_recognition_time = current_time
                
                # Log stats every 2 seconds
                if current_time - last_recognition_time >= 2.0 and len(audio_buffer) > 0:
                    logger.info(f"📊 Buffering: {chunk_count} chunks | {len(audio_buffer)} bytes | {buffer_duration:.1f}s audio")
            
            # JSON = TTS request
            else:
                try:
                    data = json.loads(message)
                    text = data.get("text", "")
                    
                    if text:
                        logger.info(f"📤 TTS Request: '{text}'")
                        audio = await bridge.text_to_speech(text)
                        if audio:
                            await websocket.send(audio)
                            logger.info(f"📥 Sent TTS audio: {len(audio)} bytes")
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON: {message}")
        
        # Process any remaining audio
        if len(audio_buffer) > 0:
            logger.info(f"Processing final {len(audio_buffer)} bytes...")
            wav_data = create_wav(audio_buffer, SAMPLE_RATE, CHANNELS, SAMPLE_WIDTH)
            await bridge.recognize_continuous(websocket, wav_data)
                    
    except websockets.exceptions.ConnectionClosed:
        logger.info(f"═══ ESP32 disconnected: {client_ip} ═══")
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)

def create_wav(audio_data, sample_rate, channels, sample_width):
    """Create WAV file from raw PCM data"""
    import struct
    
    # WAV file header
    wav_header = bytearray()
    
    # RIFF chunk
    wav_header.extend(b'RIFF')
    wav_header.extend(struct.pack('<I', len(audio_data) + 36))  # File size - 8
    wav_header.extend(b'WAVE')
    
    # fmt chunk
    wav_header.extend(b'fmt ')
    wav_header.extend(struct.pack('<I', 16))  # fmt chunk size
    wav_header.extend(struct.pack('<H', 1))   # PCM format
    wav_header.extend(struct.pack('<H', channels))
    wav_header.extend(struct.pack('<I', sample_rate))
    wav_header.extend(struct.pack('<I', sample_rate * channels * sample_width))  # Byte rate
    wav_header.extend(struct.pack('<H', channels * sample_width))  # Block align
    wav_header.extend(struct.pack('<H', sample_width * 8))  # Bits per sample
    
    # data chunk
    wav_header.extend(b'data')
    wav_header.extend(struct.pack('<I', len(audio_data)))
    
    return bytes(wav_header + audio_data)

async def main():
    logger.info("═" * 60)
    logger.info(f"🚀 WebSocket Server Starting on Railway.app (REST API Mode)")
    logger.info(f"   Host: {WS_HOST}:{WS_PORT}")
    logger.info(f"   Azure Region: {AZURE_REGION}")
    logger.info(f"   Audio: {SAMPLE_RATE}Hz, {CHANNELS} channel, 16-bit")
    logger.info(f"   Mode: Azure REST API (Railway-compatible)")
    logger.info("═" * 60)
    
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
