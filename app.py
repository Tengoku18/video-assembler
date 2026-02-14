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
                shutil.rmtree(item_path, ignore_errors=True)
    gc.collect()
    app.logger.info("Cleaned up all old jobs")


def cleanup_job(job_dir):
    shutil.rmtree(job_dir, ignore_errors=True)
    gc.collect()


def download_with_retry(url, dest_path, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            if os.path.getsize(dest_path) > 0:
                app.logger.info(f"Downloaded ({os.path.getsize(dest_path)} bytes)")
                return True
        except Exception as e:
            app.logger.warning(f"Download attempt {attempt + 1} failed: {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
    return False


def groq_tts_with_retry(script_text, audio_path, max_retries=MAX_RETRIES):
    for attempt in range(max_retries):
        try:
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
                    "response_format": "mp3"
                },
                timeout=120
            )
            if response.status_code == 200 and len(response.content) > 500:
                with open(audio_path, "wb") as f:
                    f.write(response.content)
                app.logger.info(f"TTS audio: {len(response.content)} bytes")
                return True
            elif response.status_code == 429:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                app.logger.warning(f"TTS failed: {response.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(RETRY_DELAY)
        except Exception as e:
            app.logger.warning(f"TTS exception: {str(e)[:100]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
    return False


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "groq_key_set": bool(GROQ_API_KEY)})


@app.route("/cleanup", methods=["POST"])
def force_cleanup():
    cleanup_all_jobs()
    return jsonify({"status": "cleaned"})


@app.route("/assemble", methods=["POST"])
def assemble():
    cleanup_all_jobs()
    app.logger.info("=== NEW JOB ===")

    data = request.json
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        script = data.get("script", "")
        video_urls = data.get("video_urls", [])
        audio_url = data.get("audio_url", "")

        if not video_urls:
            return jsonify({"error": "No video URLs"}), 400

        # Limit to 2 clips max
        video_urls = video_urls[:2]

        # ---- STEP 1: Get audio as MP3 (small file) ----
        audio_path = os.path.join(job_dir, "audio.mp3")

        if script and GROQ_API_KEY:
            if not groq_tts_with_retry(script, audio_path):
                return jsonify({"error": "TTS failed"}), 500
        elif audio_url:
            if not download_with_retry(audio_url, audio_path):
                return jsonify({"error": "Audio download failed"}), 500
        else:
            return jsonify({"error": "No audio source"}), 400

        # ---- STEP 2: Get audio duration ----
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30
        )
        if not result.stdout.strip():
            return jsonify({"error": "Cannot read audio duration"}), 500

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"Audio: {audio_duration:.1f}s")

        # Cap at 60s
        if audio_duration > 65:
            trimmed = os.path.join(job_dir, "trim.mp3")
            subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-t", "60", "-c", "copy", trimmed],
                          capture_output=True, timeout=30)
            if os.path.exists(trimmed) and os.path.getsize(trimmed) > 0:
                os.replace(trimmed, audio_path)
                audio_duration = 60.0

        time_per_clip = audio_duration / len(video_urls)
        gc.collect()

        # ---- STEP 3: Process clips ONE BY ONE ----
        ts_files = []
        for i, url in enumerate(video_urls):
            raw = os.path.join(job_dir, f"raw_{i}.mp4")
            ts = os.path.join(job_dir, f"clip_{i}.ts")

            if not download_with_retry(url, raw):
                app.logger.warning(f"Skip clip {i}")
                continue

            # Normalize to 720x1280 (not 1080x1920) to save memory
            cmd = [
                "ffmpeg", "-y", "-i", raw,
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", "25",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
                "-an", "-t", str(time_per_clip),
                "-f", "mpegts",
                ts
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            # Delete raw immediately
            os.remove(raw)
            gc.collect()

            if os.path.exists(ts) and os.path.getsize(ts) > 0:
                ts_files.append(ts)
                app.logger.info(f"Clip {i} OK ({os.path.getsize(ts)} bytes)")
            else:
                app.logger.error(f"Clip {i} failed")

        if not ts_files:
            return jsonify({"error": "All clips failed"}), 500

        # ---- STEP 4: Concat (stream copy = no memory) ----
        concat_input = "concat:" + "|".join(ts_files)
        output_path = os.path.join(job_dir, "final.mp4")

        cmd = [
            "ffmpeg", "-y",
            "-i", concat_input,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "96k",
            "-shortest",
            "-movflags", "+faststart",
            output_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        # Delete intermediates
        for ts in ts_files:
            try:
                os.remove(ts)
            except:
                pass
        try:
            os.remove(audio_path)
        except:
            pass
        gc.collect()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            size = os.path.getsize(output_path)
            app.logger.info(f"DONE! {size / (1024*1024):.1f} MB")
            return send_file(output_path, mimetype="video/mp4",
                           as_attachment=True, download_name=f"short_{job_id}.mp4")
        else:
            return jsonify({"error": "Final merge failed", "detail": result.stderr[-300:]}), 500

    except Exception as e:
        app.logger.error(f"Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
    finally:
        cleanup_job(job_dir)


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    cleanup_all_jobs()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
