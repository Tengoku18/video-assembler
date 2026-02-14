from flask import Flask, request, jsonify, send_file
import subprocess
import os
import requests
import uuid
import shutil

app = Flask(__name__)
WORK_DIR = "/tmp/video_work"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/assemble", methods=["POST"])
def assemble():
    data = request.json
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(WORK_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    try:
        script = data.get("script", "")
        video_urls = data.get("video_urls", [])
        title = data.get("title", "News")
        source = data.get("source", "")

        if not script:
            return jsonify({"error": "No script provided"}), 400
        if not video_urls:
            return jsonify({"error": "No video URLs provided"}), 400

        # 1. Generate audio using Groq TTS
        audio_path = os.path.join(job_dir, "audio.wav")
        app.logger.info("Generating TTS audio via Groq...")

        tts_response = requests.post(
            "https://api.groq.com/openai/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "canopylabs/orpheus-v1-english",
                "input": script[:4000],
                "voice": "troy",
                "response_format": "wav"
            },
            timeout=120
        )

        if tts_response.status_code != 200:
            return jsonify({
                "error": "TTS failed",
                "detail": tts_response.text[:500]
            }), 500

        with open(audio_path, "wb") as f:
            f.write(tts_response.content)
        app.logger.info(f"Audio generated: {len(tts_response.content)} bytes")

        # 2. Get audio duration
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
            capture_output=True, text=True
        )

        if not result.stdout.strip():
            return jsonify({"error": "Failed to get audio duration", "detail": result.stderr}), 500

        audio_duration = float(result.stdout.strip())
        app.logger.info(f"Audio duration: {audio_duration}s")

        # 3. Download video clips
        video_paths = []
        for i, url in enumerate(video_urls[:3]):
            vpath = os.path.join(job_dir, f"clip_{i}.mp4")
            r = requests.get(url, timeout=60)
            with open(vpath, "wb") as f:
                f.write(r.content)
            video_paths.append(vpath)
            app.logger.info(f"Video {i} downloaded: {len(r.content)} bytes")

        # 4. Calculate time per clip
        time_per_clip = audio_duration / len(video_paths)

        # 5. Normalize clips to 1080x1920
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
                app.logger.error(f"Failed clip {i}: {res.stderr[-300:]}")

        if not normalized:
            return jsonify({"error": "All clip normalizations failed"}), 500

        # 6. Concat clips
        concat_path = os.path.join(job_dir, "concat.txt")
        with open(concat_path, "w") as f:
            for npath in normalized:
                f.write(f"file '{npath}'\n")

        # 7. Final assembly: video + audio
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
        app.logger.info(f"Assembly stderr: {res.stderr[-300:]}")

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
