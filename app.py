from flask import Flask, request, jsonify, send_file
import subprocess
import os
import requests
import uuid
import shutil
import gc
import time
import logging

app = Flask(__name__)
WORK_DIR = "/tmp/video_work"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MAX_RETRIES = 3
RETRY_DELAY = 5

logging.basicConfig(level=logging.INFO)


def cleanup_all_jobs():
    if os.path.exists(WORK_DIR):
        for item in os.listdir(WORK_DIR):
            item_path = os.path.join(WORK_DIR, item)
            if os.path.isdir(item_path):
                try:
                    shutil.rmtree(item_path, ignore_errors=True)
                except:
                    pass
    gc.collect()
    app.logger.info("Cleaned up all old jobs and freed memory")


def cleanup_job(job_dir):
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except:
        pass
    gc.collect()


def get_disk_usage():
    total_size = 0
    if os.path.exists(WORK_DIR):
        for dirpath, dirnames, filenames in os.walk(WORK_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
    return total_size


def download_with_retry(url, dest_path, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            file_size = os.path.getsize(dest_path)
            if file_size > 0:
                app.logger.info(f"Downloaded {url[:80]}... ({file_size} bytes)")
                return True
        except Exception as e:
            app.logger.warning(f"Download attempt {attempt + 1} failed: {str(e)[:200]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
    return False


def groq_tts_with_retry(script_text, audio_path, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            app.logger.info(f"TTS attempt {attempt + 1}, script length: {len(script_text)} chars")
            response = requests.post(
                "https://api.groq.com/openai/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "canopylabs/orpheus-v1-english",
                    "input": script_text[:4000],
                    "voice": "troy",
                    "response_format": "wav"
                },
                timeout=120
            )
            if response.status_code == 200 and len(response.content) > 1000:
                with open(audio_path, "wb") as f:
                    f.write(response.content)
                app.logger.info(f"TTS audio generated: {len(response.content)} bytes")
                return True
            elif response.status_code == 429:
                wait_time = RETRY_DELAY * (attempt + 1)
                app.logger.warning(f"Groq rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                app.logger.warning(f"TTS attempt {attempt + 1} failed: {response.status_code} - {response.text[:300]}")
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY)
        except Exception as e:
            app.logger.warning(f"TTS attempt {attempt + 1} exception: {str(e)[:200]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
    return False


@app.route("/health", methods=["GET"])
def health():
    disk_usage = get_disk_usage()
    return jsonify({
        "status": "ok",
        "disk_usage_bytes": disk_usage,
        "disk_usage_mb": round(disk_usage / (1024 * 1024), 2),
        "groq_key_set": bool(GROQ_API_KEY)
    })


@app.route("/cleanup", methods=["POST"])
def force_cleanup():
    cleanup_all_jobs()
    return jsonify({"status": "cleaned", "disk_usage_mb": round(get_disk_usage() / (1024 * 1024), 2)})


@app.route("/assemble", methods=["POST"])
def assemble():
    # Clean up before starting
    cleanup_all_jobs()
    app.logger.info("=" * 50)
    app.logger.info("NEW VIDEO ASSEMBLY JOB STARTED")
    app.logger.info("=" * 50)

    data = request.json
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        script = data.get("script", "")
        video_urls = data.get("video_urls", [])
        title = data.get("title", "News")
        source = data.get("source", "")
        audio_url = data.get("audio_url", "")

        if not video_urls:
            return jsonify({"error": "No video URLs provided"}), 400

        # ---- STEP 1: Get audio ----
        audio_path = os.path.join(job_dir, "audio.wav")

        if script and GROQ_API_KEY:
            app.logger.info("Generating TTS audio on server...")
            if not groq_tts_with_retry(script, audio_path):
                return jsonify({"error": "TTS generation failed after retries"}), 500
        elif audio_url:
            if not download_with_retry(audio_url, audio_path):
                return jsonify({"error": "Audio download failed"}), 500
        else:
            return jsonify({"error": "No audio source. Send 'script' or 'audio_url'"}), 400

        # ---- STEP 2: Get audio duration ----
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30
        )

        if not result.stdout.strip():
            return jsonify({"error": "Could not determine audio duration"}), 500

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"Audio duration: {audio_duration:.1f}s")

        # Trim if too long
        if audio_duration > 65:
            trimmed = os.path.join(job_dir, "trimmed.wav")
            subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-t", "60", "-c", "copy", trimmed],
                          capture_output=True, timeout=30)
            if os.path.exists(trimmed):
                os.replace(trimmed, audio_path)
                audio_duration = 60.0

        # Convert audio to AAC now to save memory later
        audio_aac = os.path.join(job_dir, "audio.m4a")
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_path,
            "-c:a", "aac", "-b:a", "96k", "-ac", "1",
            audio_aac
        ], capture_output=True, timeout=60)

        # Delete original WAV to free memory
        if os.path.exists(audio_aac) and os.path.getsize(audio_aac) > 0:
            os.remove(audio_path)
            audio_path = audio_aac
            app.logger.info("Converted audio to AAC to save memory")
        gc.collect()

        # ---- STEP 3: Download and process clips ONE AT A TIME ----
        # Instead of downloading all then normalizing all,
        # we process each clip immediately and delete the original
        time_per_clip = audio_duration / min(len(video_urls), 3)
        app.logger.info(f"Time per clip: {time_per_clip:.1f}s")

        normalized = []
        for i, url in enumerate(video_urls[:3]):
            raw_path = os.path.join(job_dir, f"raw_{i}.mp4")
            norm_path = os.path.join(job_dir, f"norm_{i}.ts")  # Use .ts for seamless concat

            # Download
            if not download_with_retry(url, raw_path):
                app.logger.warning(f"Skipping clip {i}")
                continue

            # Normalize immediately - output as MPEG-TS for concat
            cmd = [
                "ffmpeg", "-y", "-i", raw_path,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", "25",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-an",
                "-t", str(time_per_clip),
                "-f", "mpegts",
                norm_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # Delete raw immediately to free memory
            try:
                os.remove(raw_path)
            except:
                pass
            gc.collect()

            if os.path.exists(norm_path) and os.path.getsize(norm_path) > 0:
                normalized.append(norm_path)
                app.logger.info(f"Clip {i} normalized OK ({os.path.getsize(norm_path)} bytes)")
            else:
                app.logger.error(f"Clip {i} failed: {result.stderr[-200:]}")

        if not normalized:
            return jsonify({"error": "All clip normalizations failed"}), 500

        # ---- STEP 4: Concat using MPEG-TS concat protocol (no re-encode!) ----
        # This uses almost zero memory compared to file-based concat
        concat_input = "concat:" + "|".join(normalized)
        concat_video = os.path.join(job_dir, "concat.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-i", concat_input,
            "-c:v", "copy",  # NO re-encode = minimal memory
            "-f", "mp4",
            concat_video
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if not os.path.exists(concat_video) or os.path.getsize(concat_video) == 0:
            app.logger.error(f"Concat failed: {result.stderr[-300:]}")
            return jsonify({"error": "Concat failed", "detail": result.stderr[-300:]}), 500

        app.logger.info(f"Concat OK ({os.path.getsize(concat_video)} bytes)")

        # Delete normalized clips to free memory
        for npath in normalized:
            try:
                os.remove(npath)
            except:
                pass
        gc.collect()

        # ---- STEP 5: Merge audio + video (stream copy video, minimal memory) ----
        output_path = os.path.join(job_dir, "final.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-i", concat_video,
            "-i", audio_path,
            "-c:v", "copy",       # Don't re-encode video
            "-c:a", "aac", "-b:a", "96k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Delete intermediates
        try:
            os.remove(concat_video)
            os.remove(audio_path)
        except:
            pass
        gc.collect()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size = os.path.getsize(output_path)
            app.logger.info(f"DONE! Video: {file_size} bytes ({file_size / (1024*1024):.1f} MB)")
            return send_file(
                output_path,
                mimetype="video/mp4",
                as_attachment=True,
                download_name=f"short_{job_id}.mp4"
            )
        else:
            return jsonify({
                "error": "Final merge failed",
                "detail": result.stderr[-500:]
            }), 500

    except Exception as e:
        app.logger.error(f"Exception: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_job(job_dir)
        app.logger.info("Job cleanup complete")


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    cleanup_all_jobs()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
