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

if __name__ == "__main__":
    os.makedirs(WORK_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
