import os
import re
import pysrt
import asyncio
from pydub import AudioSegment
import edge_tts
import requests
import json
import unicodedata
from app.core.security import decrypt_data
from dotenv import load_dotenv
import imageio_ffmpeg
import threading

# Chỉ định đường dẫn FFmpeg cho pydub để tránh lỗi WinError 2
AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

ENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../data/.env"))

class TTSGenerator:
    def __init__(self):
        self._vieneu = None
        self._vieneu_lock = threading.Lock()
        
    def _get_fpt_audio(self, text: str, voice: str) -> str:
        load_dotenv(ENV_PATH, override=True)
        api_key = decrypt_data(os.getenv("FPT_AI_API_KEY", ""))
        if not api_key:
            raise Exception("Lỗi: FPT_AI_API_KEY chưa được cấu hình!")
            
        url = "https://api.fpt.ai/hmi/tts/v5"
        payload = text.encode('utf-8')
        headers = {
            'api-key': api_key,
            'voice': voice,
            'speed': '',
            'format': 'mp3'
        }
        
        response = requests.post(url, data=payload, headers=headers)
        if response.status_code == 200:
            result = response.json()
            if "async" in result:
                audio_url = result["async"]
                import time
                for _ in range(10): # polling for 10 seconds
                    time.sleep(1)
                    audio_res = requests.get(audio_url)
                    if audio_res.status_code == 200:
                        import tempfile
                        tmp_path = os.path.join(tempfile.gettempdir(), f"fpt_{int(time.time())}.mp3")
                        with open(tmp_path, "wb") as f:
                            f.write(audio_res.content)
                        return tmp_path
            raise Exception("FPT API không trả về audio_url hợp lệ")
        else:
            raise Exception(f"Lỗi FPT API: {response.text}")

    async def _generate_edge_audio(self, text: str, voice: str, output_path: str, rate: str = "+0%", log_callback=None):
        import random
        import asyncio
        max_retries = 5
        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=rate)
                await communicate.save(output_path)
                return
            except Exception as e:
                if log_callback:
                    log_callback(f"[*] Gặp sự cố kết nối với Microsoft TTS (Lần thử {attempt + 1}/{max_retries}): {e}. Đang thử lại...\n")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1 + attempt * 1.5 + random.uniform(0, 1))
                    continue
                raise e

    def _generate_openai_audio(self, text: str, voice: str, output_path: str):
        load_dotenv(ENV_PATH, override=True)
        api_key = decrypt_data(os.getenv("OPENAI_API_KEY", ""))
        if not api_key:
            raise Exception("Lỗi: OPENAI_API_KEY chưa được cấu hình!")
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        # Strip provider prefix if present (e.g., openai_alloy -> alloy)
        if voice.startswith("openai_"):
            voice = voice.replace("openai_", "")
            
        response = client.audio.speech.create(
            model="tts-1",
            voice=voice,
            input=text
        )
        response.stream_to_file(output_path)

    def _generate_elevenlabs_audio(self, text: str, voice: str, output_path: str):
        load_dotenv(ENV_PATH, override=True)
        api_key = decrypt_data(os.getenv("ELEVENLABS_API_KEY", ""))
        if not api_key:
            raise Exception("Lỗi: ELEVENLABS_API_KEY chưa được cấu hình!")
            
        # Map our local ids to ElevenLabs voice IDs
        voice_map = {
            "elevenlabs_rachel": "21m00Tcm4TlvDq8ikWAM",
            "elevenlabs_drew": "29vD33N1CtxCmqQRPOHJ",
            "elevenlabs_clyde": "2EiwWnXFnvU5JabPnv8n",
            "elevenlabs_mimi": "zrHiDhphv9ZnVb4IGGlb"
        }
        
        voice_id = voice_map.get(voice, "21m00Tcm4TlvDq8ikWAM")
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key
        }
        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        response = requests.post(url, json=data, headers=headers)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
        else:
            raise Exception(f"Lỗi ElevenLabs API: {response.text}")

    def _get_vieneu_client(self):
        if self._vieneu is None:
            with self._vieneu_lock:
                if self._vieneu is None:  # Double-checked locking
                    try:
                        from vieneu import Vieneu
                        # Khởi tạo mô hình VieNeu-TTS
                        self._vieneu = Vieneu(emotion="natural")
                    except Exception as e:
                        raise Exception(f"Lỗi khởi tạo VieNeu-TTS (Vui lòng cài đặt eSpeak NG và pip install vieneu): {str(e)}")
        return self._vieneu

    def _generate_vieneu_audio(self, text: str, voice: str, output_path: str, reference_audio: str = None):
        client = self._get_vieneu_client()
        from app.core.config import DATA_DIR
        
        # Check if the voice is a cloned voice prefix (e.g. clone_voice_name.wav)
        if voice and voice.startswith("clone_"):
            filename = voice.replace("clone_", "")
            clone_path = os.path.join(DATA_DIR, "vieneu_clones", filename)
            if os.path.exists(clone_path):
                reference_audio = clone_path
        
        voice_data = None
        # Zero-shot Voice Cloning (using reference audio)
        if reference_audio and os.path.exists(reference_audio):
            try:
                voice_data = client.encode_reference(reference_audio)
            except Exception as e:
                print(f"Error encoding cloned voice reference {reference_audio}: {e}")
                # Fallback to default presets if reference encoding fails
                voice_data = None
                
        # If not a cloned voice or encoding failed, use preset voice
        if voice_data is None and voice and voice != "default":
            try:
                presets = client.list_preset_voices()
                target_voice_id = None
                
                if voice == "male":
                    for desc, v_id in presets:
                        desc_lower = desc.lower()
                        if "nam" in desc_lower or "male" in desc_lower:
                            target_voice_id = v_id
                            break
                elif voice == "female":
                    for desc, v_id in presets:
                        desc_lower = desc.lower()
                        if "nu" in desc_lower or "nữ" in desc_lower or "female" in desc_lower:
                            target_voice_id = v_id
                            break
                elif not voice.startswith("clone_"):  # do not match clone_ prefix as preset
                    target_voice_id = voice
                    
                if not target_voice_id and presets:
                    target_voice_id = presets[0][1]
                    
                if target_voice_id:
                    voice_data = client.get_preset_voice(target_voice_id)
            except Exception:
                voice_data = None
                
        audio = client.infer(text=text, voice=voice_data)
        client.save(audio, output_path)

    @staticmethod
    def _estimate_tts_rate(tts_text: str, target_duration_ms: int) -> str:
        """
        Hybrid Layer 1: Estimate optimal TTS speaking rate based on
        character density vs available subtitle duration.
        Vietnamese averages ~70-80ms per character at normal speed.
        Returns Edge TTS rate string like '+0%', '+8%', '+15%'.
        """
        if target_duration_ms <= 0:
            return "+0%"
        
        # Estimated TTS duration at normal speed (~75ms per Vietnamese character)
        MS_PER_CHAR = 75
        estimated_tts_ms = len(tts_text) * MS_PER_CHAR
        overflow_ratio = estimated_tts_ms / target_duration_ms
        
        if overflow_ratio > 1.25:
            return "+18%"
        elif overflow_ratio > 1.15:
            return "+12%"
        elif overflow_ratio > 1.05:
            return "+6%"
        else:
            return "+0%"

    def generate_tts_from_queue(self, srt_queue, srt_path: str, output_audio_path: str, voice_mode: str, video_path: str, log_callback, vocal_path_to_clone: str = None):
        import subprocess
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        import re
        stderr_output = result.stderr or ''
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr_output)
        total_duration_ms = 60000
        if match:
            h, m, s = match.groups()
            total_duration_ms = int((float(h) * 3600 + float(m) * 60 + float(s)) * 1000)
            
        base_audio = AudioSegment.empty()
        
        import tempfile
        tmp_dir = tempfile.gettempdir()

        from app.core.redis_pool import get_sync_redis
        sync_redis = get_sync_redis(decode_responses=True)
        base_name = os.path.basename(video_path).split('.')[0]

        def process_single_sub(i, sub, next_sub_start_ms=None):
            if sync_redis.get(f"pause_video_{base_name}") == "1":
                if log_callback: log_callback(f"[System] Phát hiện lệnh dừng từ người dùng. Hủy sinh TTS cho {base_name}...\n")
                raise Exception("Tiến trình bị hủy bởi người dùng.")

            text = (sub.text or '').replace('\n', ' ').strip()
            tag = None
            if text.startswith("[M]"):
                tag = "M"
                text = text[3:].strip()
            elif text.startswith("[F]"):
                tag = "F"
                text = text[3:].strip()
                
            sub.text = text 
            if not text: return None
            
            if "DELETE" in text.upper():
                return None
                
            tts_text = re.sub(r'[<>\*\[\]\~_\|\^\-\+]', ' ', text).strip()
            tts_text = unicodedata.normalize('NFC', tts_text)
            if not re.search(r'[a-zA-Z0-9À-ỹ]', tts_text): return None
                
            local_voice_mode = voice_mode
            voice_to_use = None
            is_fpt = False
            is_openai = False
            is_elevenlabs = False
            
            if local_voice_mode in ["auto", "edge_auto"]:
                load_dotenv(ENV_PATH, override=True)
                active_tts = os.getenv("ACTIVE_TTS_PROVIDER", "edge")
                if active_tts == "fpt": local_voice_mode = "fpt_minhquang" if tag == "M" else "fpt_banmai"
                elif active_tts == "openai": local_voice_mode = "openai_onyx" if tag == "M" else "openai_nova"
                elif active_tts == "elevenlabs": local_voice_mode = "elevenlabs_drew" if tag == "M" else "elevenlabs_rachel"
                elif active_tts == "vieneu": local_voice_mode = "vieneu_male" if tag == "M" else "vieneu_female"
                else: local_voice_mode = "edge_namminh" if tag == "M" else "edge_hoaimy"
            
            is_vieneu = False
            if local_voice_mode.startswith("edge_"): voice_to_use = "vi-VN-NamMinhNeural" if "namminh" in local_voice_mode else "vi-VN-HoaiMyNeural"
            elif local_voice_mode.startswith("fpt_"): voice_to_use = local_voice_mode.replace("fpt_", ""); is_fpt = True
            elif local_voice_mode.startswith("openai_"): voice_to_use = local_voice_mode; is_openai = True
            elif local_voice_mode.startswith("elevenlabs_"): voice_to_use = local_voice_mode; is_elevenlabs = True
            elif local_voice_mode.startswith("vieneu_"): voice_to_use = local_voice_mode.replace("vieneu_", ""); is_vieneu = True
            else: voice_to_use = "vi-VN-HoaiMyNeural"
                
            start_ms = (sub.start.hours * 3600 + sub.start.minutes * 60 + sub.start.seconds) * 1000 + sub.start.milliseconds
            end_ms = (sub.end.hours * 3600 + sub.end.minutes * 60 + sub.end.seconds) * 1000 + sub.end.milliseconds
            duration_ms = max(end_ms - start_ms, 100)
            
            # Hybrid Layer 1: Pre-speed TTS rate estimation
            rate = self._estimate_tts_rate(tts_text, duration_ms)
            
            clip_path = os.path.join(tmp_dir, f"clip_{i}.mp3")
            clip_wav_path = os.path.join(tmp_dir, f"clip_{i}.wav")
            
            try:
                if is_vieneu:
                    raw_vieneu_path = os.path.join(tmp_dir, f"raw_vieneu_{i}.wav")
                    ref_audio = vocal_path_to_clone
                    self._generate_vieneu_audio(tts_text, voice_to_use, raw_vieneu_path, reference_audio=ref_audio)
                    subprocess.run([
                        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", raw_vieneu_path,
                        "-acodec", "pcm_s16le", "-ar", "44100", clip_wav_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    if os.path.exists(raw_vieneu_path): os.remove(raw_vieneu_path)
                else:
                    if is_fpt: clip_path = self._get_fpt_audio(tts_text, voice_to_use)
                    elif is_openai: self._generate_openai_audio(tts_text, voice_to_use, clip_path)
                    elif is_elevenlabs: self._generate_elevenlabs_audio(tts_text, voice_to_use, clip_path)
                    else:
                        import asyncio
                        asyncio.run(self._generate_edge_audio(tts_text, voice_to_use, clip_path, rate=rate, log_callback=log_callback))
                        
                    subprocess.run([
                        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", clip_path, clip_wav_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                
                clip_audio = AudioSegment.from_wav(clip_wav_path)
                audio_duration_ms = len(clip_audio)
                
                # Hybrid Layer 2: Smart Spillover & Capped Fast-Forward
                target_duration_ms = max(end_ms - start_ms, 100)
                overlap = audio_duration_ms - target_duration_ms
                if overlap > 0:
                    max_allowed_duration = target_duration_ms
                    if next_sub_start_ms is not None:
                        gap = next_sub_start_ms - end_ms
                        if gap > 0:
                            max_allowed_duration += gap
                    else:
                        max_allowed_duration += 2000
                        
                    if audio_duration_ms <= max_allowed_duration:
                        pass  # Spillover safe zone
                    else:
                        needed_ratio = audio_duration_ms / max_allowed_duration
                        safe_ratio = min(needed_ratio, 1.4)
                        
                        stretched_wav_path = os.path.join(tmp_dir, f"clip_{i}_stretched.wav")
                        try:
                            subprocess.run([
                                imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", clip_wav_path,
                                "-filter:a", f"atempo={safe_ratio}", stretched_wav_path
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                            
                            clip_audio = AudioSegment.from_wav(stretched_wav_path)
                            if os.path.exists(stretched_wav_path):
                                os.remove(stretched_wav_path)
                        except Exception as speed_err:
                            if log_callback:
                                log_callback(f"[!] Lỗi ép tốc dòng {i}: {speed_err}\n")
                        
                        # Soft Trimming
                        if len(clip_audio) > max_allowed_duration:
                            clip_audio = clip_audio[:max_allowed_duration].fade_out(50)
                
                if os.path.exists(clip_path):
                    os.remove(clip_path)
                if os.path.exists(clip_wav_path):
                    os.remove(clip_wav_path)
                
                actual_end_ms = start_ms + len(clip_audio)
                return (start_ms, clip_audio, actual_end_ms, sub.index)
            except Exception as e:
                if log_callback: log_callback(f"[!] Lỗi tạo audio cho dòng {i} ('{text}'): {e}\n")
                return None

        import concurrent.futures
        max_workers = 3
        results = []
        if log_callback: log_callback(f"[*] Bắt đầu sinh âm thanh Pipeline bằng {max_workers} luồng xử lý song song...\n")
        
        all_subs = []
        prev_chunk_last_sub = None  # Track last sub of previous chunk for cross-chunk boundary
        prev_chunk_last_future = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            while True:
                chunk = srt_queue.get()
                if chunk is None: # EOF signal
                    break
                
                # Parse the chunk text into SubRipItems
                subs = pysrt.from_string(chunk)
                all_subs.extend(subs)
                
                # If we have context from previous chunk, submit the boundary sub now
                if prev_chunk_last_sub is not None and len(subs) > 0:
                    n_s = subs[0].start
                    boundary_next_start = (n_s.hours * 3600 + n_s.minutes * 60 + n_s.seconds) * 1000 + n_s.milliseconds
                    future = executor.submit(process_single_sub, prev_chunk_last_sub.index, prev_chunk_last_sub, boundary_next_start)
                    futures[future] = prev_chunk_last_sub.index
                    prev_chunk_last_sub = None
                
                for idx_sub, sub in enumerate(subs):
                    if idx_sub + 1 < len(subs):
                        # Not the last sub in this chunk - we know the next sub start
                        n_s = subs[idx_sub + 1].start
                        next_sub_start_ms = (n_s.hours * 3600 + n_s.minutes * 60 + n_s.seconds) * 1000 + n_s.milliseconds
                        future = executor.submit(process_single_sub, sub.index, sub, next_sub_start_ms)
                        futures[future] = sub.index
                    else:
                        # Last sub in this chunk - defer until we know next chunk's first sub
                        prev_chunk_last_sub = sub
                        
            # Submit the very last sub (no next chunk coming)
            if prev_chunk_last_sub is not None:
                future = executor.submit(process_single_sub, prev_chunk_last_sub.index, prev_chunk_last_sub, None)
                futures[future] = prev_chunk_last_sub.index
                    
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                except Exception as exc:
                    idx = futures[future]
                    if log_callback: log_callback(f"[!] Lỗi luồng xử lý câu {idx}: {exc}\n")

        total_subs = len(all_subs)
        success_count = len(results)
        fail_count = total_subs - success_count
        if log_callback:
            log_callback(f"[*] Kết quả TTS Pipeline: {success_count}/{total_subs} câu thành công.")
            if fail_count > 0:
                log_callback(f" ({fail_count} câu bị bỏ qua hoặc lỗi)")
            log_callback("\n")
        
        results.sort(key=lambda x: x[0])
        
        from pysrt.srttime import SubRipTime
        
        for start_ms, clip_audio, actual_end_ms, sub_idx in results:
            clip_end_ms = start_ms + len(clip_audio)
            if len(base_audio) < clip_end_ms:
                extension = clip_end_ms - len(base_audio)
                base_audio += AudioSegment.silent(duration=extension)
            base_audio = base_audio.overlay(clip_audio, position=start_ms)
            
            # Sync subtitle timing with actual TTS duration
            sub_item = next((s for s in all_subs if s.index == sub_idx), None)
            if sub_item:
                sub_item.end = SubRipTime(milliseconds=actual_end_ms)
            
        # Sync subtitle timing with actual TTS duration and filter [DELETE]
        valid_subs = []
        for s in all_subs:
            text_stripped = (s.text or '').strip()
            if "DELETE" not in text_stripped.upper():
                valid_subs.append(s)
                
        # Re-index
        for i, s in enumerate(valid_subs):
            s.index = i + 1
            
        full_srt = pysrt.SubRipFile(items=valid_subs)
        full_srt.save(srt_path, encoding='utf-8')
        
        if len(base_audio) == 0:
            if log_callback: log_callback("[!] Cảnh báo: Không sinh được audio TTS nào (hoặc lỗi toàn bộ). Tạo audio trống để tránh crash FFmpeg.\n")
            base_audio = AudioSegment.silent(duration=total_duration_ms if total_duration_ms > 0 else 5000)
            
        base_audio.export(output_audio_path, format="mp3")
        return output_audio_path

    def generate_tts_track(self, srt_path: str, output_audio_path: str, voice_mode: str, video_path: str, log_callback, vocal_path_to_clone: str = None):
        subs = pysrt.open(srt_path, encoding='utf-8')
        
        import subprocess
        # Get duration using ffmpeg_exe to avoid ffprobe dependency
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        # Parse Duration: 00:00:15.12
        import re
        stderr_output = result.stderr or ''
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr_output)
        total_duration_ms = 60000
        if match:
            h, m, s = match.groups()
            total_duration_ms = int((float(h) * 3600 + float(m) * 60 + float(s)) * 1000)
            
        # Tối ưu RAM: Khởi tạo mảng audio rỗng thay vì mảng khổng lồ bằng total_duration_ms
        base_audio = AudioSegment.empty()
        
        import tempfile
        tmp_dir = tempfile.gettempdir()

        from app.core.redis_pool import get_sync_redis
        sync_redis = get_sync_redis(decode_responses=True)
        base_name = os.path.basename(video_path).split('.')[0]


        def process_single_sub(i, sub, next_sub_start_ms=None, exact_timing=False):
            # Periodically check pause/cancellation flag to cancel TTS early
            if sync_redis.get(f"pause_video_{base_name}") == "1":
                if log_callback: log_callback(f"[System] Phát hiện lệnh dừng từ người dùng. Hủy sinh TTS cho {base_name}...\n")
                raise Exception("Tiến trình bị hủy bởi người dùng.")

            text = (sub.text or '').replace('\n', ' ').strip()
            
            tag = None
            if text.startswith("[M]"):
                tag = "M"
                text = text[3:].strip()
            elif text.startswith("[F]"):
                tag = "F"
                text = text[3:].strip()
                
            sub.text = text 
            
            if not text or "DELETE" in text.upper():
                return None
                
            tts_text = re.sub(r'[<>\*\[\]\~_\|\^\-\+]', ' ', text).strip()
            tts_text = unicodedata.normalize('NFC', tts_text)
                
            if not re.search(r'[a-zA-Z0-9À-ỹ]', tts_text):
                return None
                
            local_voice_mode = voice_mode
            voice_to_use = None
            is_fpt = False
            is_openai = False
            is_elevenlabs = False
            
            if local_voice_mode in ["auto", "edge_auto"]:
                load_dotenv(ENV_PATH, override=True)
                active_tts = os.getenv("ACTIVE_TTS_PROVIDER", "edge")
                
                if active_tts == "fpt":
                    local_voice_mode = "fpt_minhquang" if tag == "M" else "fpt_banmai"
                elif active_tts == "openai":
                    local_voice_mode = "openai_onyx" if tag == "M" else "openai_nova"
                elif active_tts == "elevenlabs":
                    local_voice_mode = "elevenlabs_drew" if tag == "M" else "elevenlabs_rachel"
                elif active_tts == "vieneu":
                    local_voice_mode = "vieneu_male" if tag == "M" else "vieneu_female"
                else:
                    local_voice_mode = "edge_namminh" if tag == "M" else "edge_hoaimy"
            
            is_vieneu = False
            if local_voice_mode.startswith("edge_"):
                voice_to_use = "vi-VN-NamMinhNeural" if "namminh" in local_voice_mode else "vi-VN-HoaiMyNeural"
            elif local_voice_mode.startswith("fpt_"):
                voice_to_use = local_voice_mode.replace("fpt_", "")
                is_fpt = True
            elif local_voice_mode.startswith("openai_"):
                voice_to_use = local_voice_mode
                is_openai = True
            elif local_voice_mode.startswith("elevenlabs_"):
                voice_to_use = local_voice_mode
                is_elevenlabs = True
            elif local_voice_mode.startswith("vieneu_"):
                voice_to_use = local_voice_mode.replace("vieneu_", "")
                is_vieneu = True
            else:
                voice_to_use = "vi-VN-HoaiMyNeural"
                
            start_ms = (sub.start.hours * 3600 + sub.start.minutes * 60 + sub.start.seconds) * 1000 + sub.start.milliseconds
            end_ms = (sub.end.hours * 3600 + sub.end.minutes * 60 + sub.end.seconds) * 1000 + sub.end.milliseconds
            target_duration_ms = max(end_ms - start_ms, 100)
            
            # Hybrid Layer 1: Pre-speed TTS rate estimation
            rate = self._estimate_tts_rate(tts_text, target_duration_ms)
            
            clip_path = os.path.join(tmp_dir, f"clip_{i}.mp3")
            clip_wav_path = os.path.join(tmp_dir, f"clip_{i}.wav")
            
            try:
                if is_vieneu:
                    raw_vieneu_path = os.path.join(tmp_dir, f"raw_vieneu_{i}.wav")
                    
                    ref_audio = vocal_path_to_clone
                    self._generate_vieneu_audio(tts_text, voice_to_use, raw_vieneu_path, reference_audio=ref_audio)
                    subprocess.run([
                        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", raw_vieneu_path,
                        "-acodec", "pcm_s16le", "-ar", "44100", clip_wav_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                    if os.path.exists(raw_vieneu_path):
                        os.remove(raw_vieneu_path)
                else:
                    if is_fpt:
                        clip_path = self._get_fpt_audio(tts_text, voice_to_use)
                    elif is_openai:
                        self._generate_openai_audio(tts_text, voice_to_use, clip_path)
                    elif is_elevenlabs:
                        self._generate_elevenlabs_audio(tts_text, voice_to_use, clip_path)
                    else:
                        import asyncio
                        asyncio.run(self._generate_edge_audio(tts_text, voice_to_use, clip_path, rate=rate, log_callback=log_callback))
                        
                    import time
                    if "edge" in local_voice_mode:
                        time.sleep(0.2)
                        
                    subprocess.run([
                        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", clip_path, clip_wav_path
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                
                clip_audio = AudioSegment.from_wav(clip_wav_path)
                audio_duration_ms = len(clip_audio)
                
                # Hybrid Layer 2: Smart Spillover & Capped Fast-Forward
                if exact_timing:
                    max_allowed_duration = target_duration_ms
                    needed_ratio = audio_duration_ms / max_allowed_duration
                    
                    if not (0.98 <= needed_ratio <= 1.02):
                        safe_ratio = min(max(needed_ratio, 0.5), 2.0)
                        stretched_wav_path = os.path.join(tmp_dir, f"clip_{i}_stretched.wav")
                        try:
                            subprocess.run([
                                imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", clip_wav_path,
                                "-filter:a", f"atempo={safe_ratio}", stretched_wav_path
                            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                            
                            clip_audio = AudioSegment.from_wav(stretched_wav_path)
                            if os.path.exists(stretched_wav_path):
                                os.remove(stretched_wav_path)
                                
                            if log_callback:
                                log_callback(f"[*] Dòng {i}: Ép/Kéo tốc độ {safe_ratio:.2f}x để vừa đúng {max_allowed_duration}ms\n")
                        except Exception as speed_err:
                            if log_callback:
                                log_callback(f"[!] Lỗi ép/kéo tốc dòng {i}: {speed_err}\n")
                                
                    # Chỉnh xác chính xác độ dài (cắt bớt hoặc thêm silence)
                    if len(clip_audio) > max_allowed_duration:
                        clip_audio = clip_audio[:max_allowed_duration].fade_out(50)
                    elif len(clip_audio) < max_allowed_duration:
                        clip_audio += AudioSegment.silent(duration=max_allowed_duration - len(clip_audio))
                else:
                    overlap = audio_duration_ms - target_duration_ms
                    if overlap > 0:
                        max_allowed_duration = target_duration_ms
                        if next_sub_start_ms is not None:
                            # Tính khoảng trống (gap) tới câu tiếp theo
                            gap = next_sub_start_ms - end_ms
                            if gap > 0:
                                max_allowed_duration += gap
                        else:
                            # Câu cuối cùng, cho phép dài hơn một chút
                            max_allowed_duration += 2000
                            
                        if audio_duration_ms <= max_allowed_duration:
                            # Nằm trong vùng an toàn (Spillover hợp lệ) -> Không cần FFmpeg
                            pass
                        else:
                            # Vượt qua mức an toàn -> Dùng FFmpeg tua nhanh (Cap at 1.4x)
                            needed_ratio = audio_duration_ms / max_allowed_duration
                            safe_ratio = min(needed_ratio, 1.4)
                            
                            stretched_wav_path = os.path.join(tmp_dir, f"clip_{i}_stretched.wav")
                            try:
                                subprocess.run([
                                    imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", clip_wav_path,
                                    "-filter:a", f"atempo={safe_ratio}", stretched_wav_path
                                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                                
                                clip_audio = AudioSegment.from_wav(stretched_wav_path)
                                if os.path.exists(stretched_wav_path):
                                    os.remove(stretched_wav_path)
                                    
                                if log_callback:
                                    log_callback(f"[*] Dòng {i}: Ép tốc {safe_ratio:.2f}x (Max allowed: {max_allowed_duration}ms)\n")
                            except Exception as speed_err:
                                if log_callback:
                                    log_callback(f"[!] Lỗi ép tốc dòng {i}: {speed_err}\n")
                            
                            # Soft Trimming nếu sau khi ép tốc (1.25x) vẫn thừa (Fade out mượt 50ms)
                            if len(clip_audio) > max_allowed_duration:
                                clip_audio = clip_audio[:max_allowed_duration].fade_out(50)
                                if log_callback:
                                    log_callback(f"[*] Dòng {i}: Đã cắt gọt (Soft Trimming) phần dư thừa.\n")
                
                if os.path.exists(clip_path):
                    os.remove(clip_path)
                if os.path.exists(clip_wav_path):
                    os.remove(clip_wav_path)
                    
                actual_end_ms = start_ms + len(clip_audio)
                return (start_ms, clip_audio, actual_end_ms, sub.index)
            except Exception as e:
                if log_callback: log_callback(f"[!] Lỗi tạo audio cho dòng {i} ('{text}'): {e}\n")
                return None

        import concurrent.futures
        
        # Chạy đa luồng! Giữ 3 worker cho nhánh Edit (sequential) để tránh rate limit
        max_workers = 3
        results = []
        
        if log_callback: log_callback(f"[*] Bắt đầu sinh âm thanh bằng {max_workers} luồng xử lý song song...\n")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for i, sub in enumerate(subs):
                next_sub_start_ms = None
                if i + 1 < len(subs):
                    n_s = subs[i+1].start
                    next_sub_start_ms = (n_s.hours * 3600 + n_s.minutes * 60 + n_s.seconds) * 1000 + n_s.milliseconds
                futures[executor.submit(process_single_sub, i, sub, next_sub_start_ms, exact_timing=True)] = i
                
            for future in concurrent.futures.as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        results.append(res)
                except Exception as exc:
                    idx = futures[future]
                    if log_callback: log_callback(f"[!] Lỗi luồng xử lý câu {idx}: {exc}\n")
        
        total_subs = len(subs)
        success_count = len(results)
        fail_count = total_subs - success_count
        if log_callback:
            log_callback(f"[*] Kết quả TTS Track: {success_count}/{total_subs} câu thành công.")
            if fail_count > 0:
                log_callback(f" ({fail_count} câu bị bỏ qua hoặc lỗi)")
            log_callback("\n")
        
        # Lắp ghép các đoạn âm thanh đã thu được theo đúng timestamp 
        results.sort(key=lambda x: x[0])
        
        from pysrt.srttime import SubRipTime
        
        for start_ms, clip_audio, actual_end_ms, sub_idx in results:
            clip_end_ms = start_ms + len(clip_audio)
            # Nếu base_audio hiện tại chưa đủ dài để chứa clip mới, ta chèn thêm khoảng im lặng (silence)
            if len(base_audio) < clip_end_ms:
                extension = clip_end_ms - len(base_audio)
                base_audio += AudioSegment.silent(duration=extension)
                
            base_audio = base_audio.overlay(clip_audio, position=start_ms)
            
            # Sync subtitle timing with actual TTS duration
            sub_item = next((s for s in subs if s.index == sub_idx), None)
            if sub_item:
                sub_item.end = SubRipTime(milliseconds=actual_end_ms)
            
        # Đảm bảo track lồng tiếng có một chút padding ở cuối (nếu cần thiết, FFmpeg amix duration=first sẽ lo phần còn lại)
        
        # Save clean srt
        valid_subs = []
        for s in subs:
            text_stripped = (s.text or '').strip()
            if "DELETE" not in text_stripped.upper():
                valid_subs.append(s)
                
        for i, s in enumerate(valid_subs):
            s.index = i + 1
            
        full_srt = pysrt.SubRipFile(items=valid_subs)
        full_srt.save(srt_path, encoding='utf-8')
        
        if len(base_audio) == 0:
            if log_callback: log_callback("[!] Cảnh báo: Không sinh được audio TTS nào (hoặc lỗi toàn bộ). Tạo audio trống để tránh crash FFmpeg.\n")
            base_audio = AudioSegment.silent(duration=total_duration_ms if total_duration_ms > 0 else 5000)
            
        base_audio.export(output_audio_path, format="mp3")
        return output_audio_path
