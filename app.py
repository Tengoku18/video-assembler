from flask import Flask, request, jsonify, send_file
import subprocess
import os
import requests
import uuid
import shutil

app = Flask(__name__)
WORK_DIR = "/tmp/video_work"

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/assemble", methods=["POST"])
def assemble_video():
    data = request.json
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        # 1. Download audio
        audio_path = os.path.join(job_dir, "audio.wav")
        audio_url = data.get("audio_url")
        audio_base64 = data.get("audio_base64")

        if audio_url:
            r = requests.get(audio_url, timeout=60)
            with open(audio_path, "wb") as f:
                f.write(r.content)
            app.logger.info(f"Audio downloaded: {len(r.content)} bytes")
        elif audio_base64:
            import base64
            with open(audio_path, "wb") as f:
                f.write(base64.b64decode(audio_base64))
            app.logger.info("Audio decoded from base64")
        else:
            return jsonify({"error": "No audio provided"}), 400

        # 2. Get audio duration
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True
        )
        app.logger.info(f"ffprobe stdout: {result.stdout}")
        app.logger.info(f"ffprobe stderr: {result.stderr}")

        if not result.stdout.strip():
            return jsonify({"error": "Failed to determine audio duration", "detail": result.stderr}), 400

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"Audio duration: {audio_duration}s")

        # 3. Download video clips (use SD quality to save memory)
        video_urls = data.get("video_urls", [])
        video_paths = []
        for i, url in enumerate(video_urls[:3]):
            vpath = os.path.join(job_dir, f"clip_{i}.mp4")
            r = requests.get(url, timeout=60)
            with open(vpath, "wb") as f:
                f.write(r.content)
            video_paths.append(vpath)
            app.logger.info(f"Video {i} downloaded: {len(r.content)} bytes")

        if not video_paths:
            return jsonify({"error": "No video clips provided"}), 400

        # 4. Calculate time per clip
        time_per_clip = audio_duration / len(video_paths)

        # 5. Normalize clips to 1080x1920 vertical
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
            res = subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(npath) and os.path.getsize(npath) > 0:
                normalized.append(npath)
                app.logger.info(f"Normalized clip {i} OK")
            else:
                app.logger.error(f"Normalize clip {i} failed: {res.stderr[-500:]}")

        if not normalized:
            return jsonify({"error": "All video normalizations failed"}), 500

        # 6. Create concat file
        concat_path = os.path.join(job_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for npath in normalized:
                f.write(f"file '{npath}'\n")

        # 7. Concatenate + add audio (NO text overlay to avoid font issues)
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
        res = subprocess.run(cmd, capture_output=True, text=True)
        app.logger.info(f"Final assembly stderr: {res.stderr[-500:]}")

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return send_file(
                output_path,
                mimetype="video/mp4",
                as_attachment=True,
                download_name=f"short_{job_id}.mp4"
            )
        else:
            return jsonify({"error": "Final video not created", "detail": res.stderr[-500:]}), 500

    except Exception as e:
        app.logger.error(f"Exception: {str(e)}")
        return jsonify({"error": str(e)}), 500

    finally:
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except:
            pass

@app.route("/assemble-upload", methods=["POST"])
def assemble_upload():
    """
    New endpoint that accepts multipart/form-data with:
    - audio: binary audio file
    - video_urls: comma-separated string of video URLs
    - title: string (optional, not used in overlay)
    - source: string (optional, not used in overlay)
    """
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    app.logger.info(f"[{job_id}] New /assemble-upload request started")

    try:
        # 1. Validate and save uploaded audio file
        if 'audio' not in request.files:
            app.logger.error(f"[{job_id}] No audio file in request")
            return jsonify({"error": "No audio file provided"}), 400

        audio_file = request.files['audio']
        if audio_file.filename == '':
            app.logger.error(f"[{job_id}] Empty audio filename")
            return jsonify({"error": "Empty audio filename"}), 400

        audio_path = os.path.join(job_dir, "audio.mp3")
        audio_file.save(audio_path)
        audio_size = os.path.getsize(audio_path)
        app.logger.info(f"[{job_id}] Audio file saved: {audio_size} bytes")

        # 2. Get audio duration using ffprobe
        app.logger.info(f"[{job_id}] Detecting audio duration...")
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True
        )

        if not result.stdout.strip():
            app.logger.error(f"[{job_id}] ffprobe failed: {result.stderr}")
            return jsonify({"error": "Failed to determine audio duration", "detail": result.stderr}), 400

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"[{job_id}] Audio duration: {audio_duration:.2f}s")

        # 3. Parse video URLs from comma-separated string
        video_urls_str = request.form.get('video_urls', '')
        if not video_urls_str:
            app.logger.error(f"[{job_id}] No video URLs provided")
            return jsonify({"error": "No video URLs provided"}), 400

        video_urls = [url.strip() for url in video_urls_str.split(',') if url.strip()]
        app.logger.info(f"[{job_id}] Found {len(video_urls)} video URLs to process")

        # Get optional title and source (for logging purposes)
        title = request.form.get('title', 'Untitled')
        source = request.form.get('source', 'Unknown')
        app.logger.info(f"[{job_id}] Title: '{title}', Source: '{source}'")

        if not video_urls:
            app.logger.error(f"[{job_id}] No valid video URLs after parsing")
            return jsonify({"error": "No valid video URLs provided"}), 400

        # 4. Download video clips
        video_paths = []
        for i, url in enumerate(video_urls[:5]):  # Limit to 5 clips
            vpath = os.path.join(job_dir, f"clip_{i}.mp4")
            app.logger.info(f"[{job_id}] Downloading video {i+1}/{len(video_urls[:5])}: {url[:60]}...")

            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                with open(vpath, "wb") as f:
                    f.write(r.content)
                video_size = len(r.content)
                video_paths.append(vpath)
                app.logger.info(f"[{job_id}] Video {i+1} downloaded: {video_size} bytes")
            except Exception as e:
                app.logger.error(f"[{job_id}] Failed to download video {i+1}: {str(e)}")
                # Continue with other videos
                continue

        if not video_paths:
            app.logger.error(f"[{job_id}] No video clips downloaded successfully")
            return jsonify({"error": "Failed to download any video clips"}), 400

        app.logger.info(f"[{job_id}] Successfully downloaded {len(video_paths)} videos")

        # 5. Calculate time per clip
        time_per_clip = audio_duration / len(video_paths)
        app.logger.info(f"[{job_id}] Time per clip: {time_per_clip:.2f}s")

        # 6. Normalize clips to 1080x1920 vertical
        app.logger.info(f"[{job_id}] Starting video normalization...")
        normalized = []
        for i, vpath in enumerate(video_paths):
            npath = os.path.join(job_dir, f"norm_{i}.mp4")
            app.logger.info(f"[{job_id}] Normalizing clip {i+1}/{len(video_paths)}...")

            cmd = [
                "ffmpeg", "-y", "-i", vpath,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", "25", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "28", "-an", "-t", str(time_per_clip),
                npath
            ]

            res = subprocess.run(cmd, capture_output=True, text=True)

            if os.path.exists(npath) and os.path.getsize(npath) > 0:
                norm_size = os.path.getsize(npath)
                normalized.append(npath)
                app.logger.info(f"[{job_id}] Clip {i+1} normalized successfully: {norm_size} bytes")
            else:
                app.logger.error(f"[{job_id}] Normalize clip {i+1} failed: {res.stderr[-500:]}")

        if not normalized:
            app.logger.error(f"[{job_id}] All video normalizations failed")
            return jsonify({"error": "All video normalizations failed"}), 500

        app.logger.info(f"[{job_id}] Successfully normalized {len(normalized)}/{len(video_paths)} clips")

        # 7. Create concat file
        concat_path = os.path.join(job_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for npath in normalized:
                f.write(f"file '{npath}'\n")
        app.logger.info(f"[{job_id}] Concat file created with {len(normalized)} entries")

        # 8. Concatenate videos + add audio (NO text overlay)
        app.logger.info(f"[{job_id}] Starting final assembly (concat + audio mix)...")
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

        res = subprocess.run(cmd, capture_output=True, text=True)
        app.logger.info(f"[{job_id}] Final assembly completed")

        if res.stderr:
            app.logger.info(f"[{job_id}] FFmpeg stderr (last 500 chars): {res.stderr[-500:]}")

        # 9. Verify output file
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            final_size = os.path.getsize(output_path)
            app.logger.info(f"[{job_id}] Final video created successfully: {final_size} bytes")
            app.logger.info(f"[{job_id}] Sending file to client...")

            return send_file(
                output_path,
                mimetype="video/mp4",
                as_attachment=True,
                download_name=f"short_{job_id}.mp4"
            )
        else:
            app.logger.error(f"[{job_id}] Final video not created or empty")
            return jsonify({"error": "Final video not created", "detail": res.stderr[-500:]}), 500

    except Exception as e:
        app.logger.error(f"[{job_id}] Exception occurred: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    finally:
        # 10. Cleanup temp files
        try:
            app.logger.info(f"[{job_id}] Cleaning up temp directory...")
            shutil.rmtree(job_dir, ignore_errors=True)
            app.logger.info(f"[{job_id}] Cleanup completed")
        except Exception as e:
            app.logger.warning(f"[{job_id}] Cleanup failed: {str(e)}")

if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
