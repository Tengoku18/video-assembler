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
RETRY_DELAY = 5  # seconds

# Configure logging
logging.basicConfig(level=logging.INFO)


# ============================================================
# CLEANUP HELPERS
# ============================================================

def cleanup_all_jobs():
    """Remove ALL old job directories to free memory"""
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
    """Clean a specific job directory"""
    try:
        shutil.rmtree(job_dir, ignore_errors=True)
    except:
        pass
    gc.collect()


def get_disk_usage():
    """Get current disk usage of work directory"""
    total_size = 0
    if os.path.exists(WORK_DIR):
        for dirpath, dirnames, filenames in os.walk(WORK_DIR):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
    return total_size


# ============================================================
# RETRY HELPERS
# ============================================================

def download_with_retry(url, dest_path, max_retries=MAX_RETRIES):
    """Download a file with retry logic"""
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
            else:
                app.logger.warning(f"Empty file from {url[:80]}... attempt {attempt + 1}")
        except Exception as e:
            app.logger.warning(f"Download attempt {attempt + 1} failed: {str(e)[:200]}")
            if attempt < max_retries - 1:
                time.sleep(RETRY_DELAY)
    return False


def groq_tts_with_retry(script_text, audio_path, max_retries=MAX_RETRIES):
    """Generate TTS audio using Groq with retry logic"""
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


def run_ffmpeg_with_retry(cmd, description="FFmpeg command", max_retries=2):
    """Run an FFmpeg command with retry"""
    for attempt in range(max_retries):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode == 0:
                app.logger.info(f"{description} succeeded")
                return result
            else:
                app.logger.warning(f"{description} attempt {attempt + 1} failed: {result.stderr[-300:]}")
                if attempt < max_retries - 1:
                    time.sleep(2)
        except subprocess.TimeoutExpired:
            app.logger.warning(f"{description} timed out on attempt {attempt + 1}")
        except Exception as e:
            app.logger.warning(f"{description} exception: {str(e)[:200]}")
    return None


# ============================================================
# ENDPOINTS
# ============================================================

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
    """Emergency cleanup endpoint"""
    cleanup_all_jobs()
    return jsonify({"status": "cleaned", "disk_usage_mb": round(get_disk_usage() / (1024 * 1024), 2)})


@app.route("/assemble", methods=["POST"])
def assemble():
    # ---- STEP 0: Clean up before starting ----
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

        # Also support audio_url for backward compatibility
        audio_url = data.get("audio_url", "")
        audio_base64 = data.get("audio_base64", "")

        if not video_urls:
            return jsonify({"error": "No video URLs provided"}), 400

        # ---- STEP 1: Get audio ----
        audio_path = os.path.join(job_dir, "audio.wav")

        if script and GROQ_API_KEY:
            # Generate TTS on server
            app.logger.info("Generating TTS audio on server...")
            if not groq_tts_with_retry(script, audio_path):
                return jsonify({"error": "TTS generation failed after retries"}), 500

        elif audio_url:
            # Download audio from URL
            app.logger.info(f"Downloading audio from URL...")
            if not download_with_retry(audio_url, audio_path):
                return jsonify({"error": "Audio download failed after retries"}), 500

        elif audio_base64:
            # Decode base64 audio
            import base64
            try:
                with open(audio_path, "wb") as f:
                    f.write(base64.b64decode(audio_base64))
                app.logger.info("Audio decoded from base64")
            except Exception as e:
                return jsonify({"error": f"Base64 decode failed: {str(e)}"}), 400
        else:
            return jsonify({"error": "No audio source provided. Send 'script', 'audio_url', or 'audio_base64'"}), 400

        # ---- STEP 2: Get audio duration ----
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True, timeout=30
        )

        if not result.stdout.strip():
            return jsonify({
                "error": "Could not determine audio duration",
                "detail": result.stderr[:300]
            }), 500

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"Audio duration: {audio_duration:.1f}s")

        if audio_duration > 120:
            app.logger.warning(f"Audio is {audio_duration}s, trimming to 60s")
            trimmed_path = os.path.join(job_dir, "audio_trimmed.wav")
            subprocess.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-t", "60", "-c", "copy", trimmed_path
            ], capture_output=True, timeout=30)
            if os.path.exists(trimmed_path):
                os.replace(trimmed_path, audio_path)
                audio_duration = 60.0

        # ---- STEP 3: Download video clips ----
        video_paths = []
        for i, url in enumerate(video_urls[:3]):
            vpath = os.path.join(job_dir, f"clip_{i}.mp4")
            if download_with_retry(url, vpath):
                video_paths.append(vpath)
            else:
                app.logger.warning(f"Skipping clip {i} - download failed")

        if not video_paths:
            return jsonify({"error": "All video downloads failed"}), 500

        app.logger.info(f"Downloaded {len(video_paths)} video clips")

        # ---- STEP 4: Calculate time per clip ----
        time_per_clip = audio_duration / len(video_paths)
        app.logger.info(f"Time per clip: {time_per_clip:.1f}s")

        # ---- STEP 5: Normalize clips to 1080x1920 vertical ----
        normalized = []
        for i, vpath in enumerate(video_paths):
            npath = os.path.join(job_dir, f"norm_{i}.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", vpath,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", "25", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "28", "-an", "-t", str(time_per_clip),
                npath
            ]
            result = run_ffmpeg_with_retry(cmd, f"Normalize clip {i}")
            if result and os.path.exists(npath) and os.path.getsize(npath) > 0:
                normalized.append(npath)
                # Delete original clip to free memory
                os.remove(vpath)
            else:
                app.logger.error(f"Clip {i} normalization failed completely")

        if not normalized:
            return jsonify({"error": "All clip normalizations failed"}), 500

        app.logger.info(f"Normalized {len(normalized)} clips")

        # ---- STEP 6: Concatenate clips ----
        concat_path = os.path.join(job_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for npath in normalized:
                f.write(f"file '{npath}'\n")

        # ---- STEP 7: Final assembly ----
        output_path = os.path.join(job_dir, "final.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", concat_path,
            "-i", audio_path,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart",
            output_path
        ]

        result = run_ffmpeg_with_retry(cmd, "Final assembly")

        if result and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size = os.path.getsize(output_path)
            app.logger.info(f"Video created: {file_size} bytes ({file_size / (1024*1024):.1f} MB)")

            # Delete everything except final output to free memory
            for npath in normalized:
                try:
                    os.remove(npath)
                except:
                    pass
            try:
                os.remove(audio_path)
            except:
                pass
            gc.collect()

            return send_file(
                output_path,
                mimetype="video/mp4",
                as_attachment=True,
                download_name=f"short_{job_id}.mp4"
            )
        else:
            stderr = result.stderr[-500:] if result else "FFmpeg command failed"
            return jsonify({"error": "Final video not created", "detail": stderr}), 500

    except Exception as e:
        app.logger.error(f"Exception: {str(e)}")
        return jsonify({"error": str(e)}), 500

    finally:
        cleanup_job(job_dir)
        app.logger.info("Job cleanup complete")
        app.logger.info("=" * 50)


if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    cleanup_all_jobs()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
