import os
import tempfile
import base64
import subprocess
from flask import Flask, request, jsonify, send_file
from werkzeug.utils import secure_filename
import requests
import logging
from pathlib import Path
import shutil

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
MAX_VIDEO_CLIPS = 5
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FFMPEG_PRESET = "ultrafast"

def download_file(url, destination):
    """Download a file from URL to destination."""
    try:
        logger.info(f"Downloading from {url}")
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()

        with open(destination, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Downloaded to {destination}")
        return True
    except Exception as e:
        logger.error(f"Download failed for {url}: {str(e)}")
        return False

def get_audio_duration(audio_path):
    """Get duration of audio file in seconds using ffprobe."""
    try:
        cmd = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
        logger.info(f"Audio duration: {duration}s")
        return duration
    except Exception as e:
        logger.error(f"Failed to get audio duration: {str(e)}")
        return None

def normalize_video_clip(input_path, output_path, target_duration=None):
    """Normalize video to 1080x1920 vertical format."""
    try:
        logger.info(f"Normalizing {input_path}")

        # Build FFmpeg command
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f'scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}',
            '-c:v', 'libx264',
            '-preset', FFMPEG_PRESET,
            '-crf', '23',
            '-an',  # Remove audio from video clips
            '-y'
        ]

        # Add duration if specified
        if target_duration:
            cmd.extend(['-t', str(target_duration)])

        cmd.append(output_path)

        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Normalized to {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg normalization failed: {e.stderr.decode()}")
        return False
    except Exception as e:
        logger.error(f"Normalization error: {str(e)}")
        return False

def concatenate_videos(video_paths, output_path):
    """Concatenate multiple video files."""
    try:
        # Create concat file
        concat_file = output_path + '.txt'
        with open(concat_file, 'w') as f:
            for video_path in video_paths:
                f.write(f"file '{video_path}'\n")

        logger.info(f"Concatenating {len(video_paths)} videos")

        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            '-y',
            output_path
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        # Clean up concat file
        os.remove(concat_file)

        logger.info(f"Concatenated to {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg concatenation failed: {e.stderr.decode()}")
        return False
    except Exception as e:
        logger.error(f"Concatenation error: {str(e)}")
        return False

def add_text_overlays_and_audio(video_path, audio_path, output_path, title, source):
    """Add text overlays and mix audio with video."""
    try:
        logger.info("Adding text overlays and mixing audio")

        # Escape special characters for FFmpeg
        title_escaped = title.replace("'", "'\\''").replace(":", "\\:")
        source_escaped = source.replace("'", "'\\''").replace(":", "\\:")

        # Build complex filter for text overlays
        # Title at top: white text, bold, black border
        # Source at bottom: yellow text, black border
        filter_complex = (
            f"drawtext=text='{title_escaped}':"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
            f"fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=50,"
            f"drawtext=text='{source_escaped}':"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
            f"fontsize=36:fontcolor=yellow:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-100"
        )

        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-i', audio_path,
            '-filter_complex', filter_complex,
            '-map', '0:v',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-preset', FFMPEG_PRESET,
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',  # Match video length to audio
            '-movflags', '+faststart',  # Web-optimized
            '-y',
            output_path
        ]

        subprocess.run(cmd, check=True, capture_output=True)
        logger.info(f"Final video created: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg final processing failed: {e.stderr.decode()}")
        return False
    except Exception as e:
        logger.error(f"Final processing error: {str(e)}")
        return False

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'service': 'video-assembler',
        'ffmpeg_available': shutil.which('ffmpeg') is not None
    }), 200

@app.route('/assemble', methods=['POST'])
def assemble_video():
    """Main endpoint to assemble video from audio and video clips."""
    temp_dir = None

    try:
        # Parse request
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Validate required fields
        if 'video_urls' not in data or not data['video_urls']:
            return jsonify({'error': 'video_urls array is required'}), 400

        if 'audio_base64' not in data and 'audio_url' not in data:
            return jsonify({'error': 'Either audio_base64 or audio_url is required'}), 400

        video_urls = data['video_urls']
        title = data.get('title', 'Video Title')
        source = data.get('source', 'Source')

        # Validate video URLs count
        if len(video_urls) > MAX_VIDEO_CLIPS:
            return jsonify({'error': f'Maximum {MAX_VIDEO_CLIPS} video clips allowed'}), 400

        if len(video_urls) == 0:
            return jsonify({'error': 'At least one video URL is required'}), 400

        # Create temporary directory
        temp_dir = tempfile.mkdtemp()
        logger.info(f"Created temp directory: {temp_dir}")

        # Handle audio input
        audio_path = os.path.join(temp_dir, 'audio.mp3')

        if 'audio_base64' in data and data['audio_base64']:
            # Decode base64 audio
            try:
                audio_data = base64.b64decode(data['audio_base64'])
                with open(audio_path, 'wb') as f:
                    f.write(audio_data)
                logger.info("Audio decoded from base64")
            except Exception as e:
                return jsonify({'error': f'Failed to decode audio_base64: {str(e)}'}), 400
        else:
            # Download audio from URL
            if not download_file(data['audio_url'], audio_path):
                return jsonify({'error': 'Failed to download audio file'}), 400

        # Get audio duration
        audio_duration = get_audio_duration(audio_path)
        if audio_duration is None:
            return jsonify({'error': 'Failed to determine audio duration'}), 400

        # Calculate duration per video clip
        clip_duration = audio_duration / len(video_urls)
        logger.info(f"Each clip will be {clip_duration}s ({len(video_urls)} clips)")

        # Download and normalize video clips
        normalized_clips = []
        for i, video_url in enumerate(video_urls):
            raw_clip = os.path.join(temp_dir, f'raw_{i}.mp4')
            normalized_clip = os.path.join(temp_dir, f'normalized_{i}.mp4')

            # Download
            if not download_file(video_url, raw_clip):
                return jsonify({'error': f'Failed to download video {i+1}'}), 400

            # Normalize with target duration
            if not normalize_video_clip(raw_clip, normalized_clip, clip_duration):
                return jsonify({'error': f'Failed to normalize video {i+1}'}), 400

            normalized_clips.append(normalized_clip)

        # Concatenate all normalized clips
        concatenated_path = os.path.join(temp_dir, 'concatenated.mp4')
        if not concatenate_videos(normalized_clips, concatenated_path):
            return jsonify({'error': 'Failed to concatenate videos'}), 400

        # Add text overlays and mix audio
        final_path = os.path.join(temp_dir, 'final.mp4')
        if not add_text_overlays_and_audio(concatenated_path, audio_path, final_path, title, source):
            return jsonify({'error': 'Failed to create final video'}), 400

        # Send file
        logger.info("Sending final video")
        return send_file(
            final_path,
            mimetype='video/mp4',
            as_attachment=True,
            download_name='assembled_video.mp4'
        )

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

    finally:
        # Clean up temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.error(f"Failed to clean up temp directory: {str(e)}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
