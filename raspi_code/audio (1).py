import sounddevice as sd
import scipy.io.wavfile as wav

# -------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------
SAMPLE_RATE = 16000  # 16kHz audio sampling rate
DURATION = 5          # Duration to record in seconds
OUTPUT_FILE = "recorded_audio.wav"

def record_audio():
    print("==================================================")
    print(f"[+] Recording started... Speak into the USB mic ({DURATION}s)")
    print("==================================================")
    
    # 1. Opens USB microphone channel and records digital audio stream
    audio_data = sd.rec(
        int(DURATION * SAMPLE_RATE), 
        samplerate=SAMPLE_RATE, 
        channels=1, 
        dtype='int16'
    )
    
    # 2. Holds execution until the duration completes
    sd.wait()
    
    # 3. Saves raw audio buffer to a WAV file on the Raspberry Pi
    wav.write(OUTPUT_FILE, SAMPLE_RATE, audio_data)
    
    print(f"[+] Recording finished! Audio saved as: {OUTPUT_FILE}\n")

if __name__ == "__main__":
    # Command the microphone to record
    input("Press ENTER to start recording...")
    record_audio()