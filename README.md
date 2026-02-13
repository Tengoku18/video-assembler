# Video Assembler API

A free, cloud-hosted video assembly API that combines audio and video clips into vertical YouTube Shorts-style videos (1080x1920). Built with Flask, FFmpeg, and deployable on Render.com's free tier.

## Features

- 🎬 Assembles up to 5 video clips into a single vertical video
- 🎵 Supports audio input via base64 or direct URL
- 📝 Adds customizable text overlays (title at top, source at bottom)
- ⚡ Optimized for web with fast-start MP4
- 🐳 Fully Dockerized with FFmpeg
- 🆓 Runs on Render.com free tier (no credit card required)

## API Endpoints

### `GET /health`
Health check endpoint to verify the service is running.

**Response:**
```json
{
  "status": "healthy",
  "service": "video-assembler",
  "ffmpeg_available": true
}
```

### `POST /assemble`
Main endpoint to assemble videos.

**Request Body:**
```json
{
  "audio_base64": "base64_encoded_audio_data",  // OR use audio_url
  "audio_url": "https://example.com/audio.mp3", // Alternative to audio_base64
  "video_urls": [
    "https://example.com/clip1.mp4",
    "https://example.com/clip2.mp4",
    "https://example.com/clip3.mp4"
  ],
  "title": "Your Video Title",
  "source": "Source: Your Source"
}
```

**Parameters:**
- `audio_base64` (string, optional): Base64-encoded audio file (from Groq TTS or similar)
- `audio_url` (string, optional): Direct URL to audio file (either this or audio_base64 required)
- `video_urls` (array, required): 1-5 video clip URLs (e.g., from Pexels API)
- `title` (string, optional): Text to display at the top of the video
- `source` (string, optional): Text to display at the bottom of the video

**Response:**
- Success: Returns MP4 file as download
- Error: JSON with error message

## Deploy to Render.com

### One-Click Deploy

1. Fork or push this repository to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/)
3. Click **"New +"** → **"Blueprint"**
4. Connect your GitHub repository
5. Render will automatically detect `render.yaml` and deploy

### Manual Deploy

1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repository
4. Configure:
   - **Name:** video-assembler
   - **Environment:** Docker
   - **Plan:** Free
   - **Health Check Path:** /health
5. Click **"Create Web Service"**

Your API will be live at: `https://video-assembler-XXXX.onrender.com`

## Testing

### Test Health Endpoint

```bash
curl https://your-app.onrender.com/health
```

### Test Video Assembly

```bash
curl -X POST https://your-app.onrender.com/assemble \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/sample-audio.mp3",
    "video_urls": [
      "https://player.vimeo.com/external/video1.mp4",
      "https://player.vimeo.com/external/video2.mp4"
    ],
    "title": "My Awesome Video",
    "source": "Source: Example.com"
  }' \
  --output final_video.mp4
```

## Integration with n8n

### Workflow Overview

1. **RSS Read** node: Fetch content
2. **Groq TTS** node: Generate audio from text
3. **Pexels** node: Fetch video clips
4. **HTTP Request** node: Call this API

### HTTP Request Node Configuration

**Method:** POST
**URL:** `https://your-app.onrender.com/assemble`
**Authentication:** None
**Send Body:** Yes
**Body Content Type:** JSON

**Body (Expression):**
```javascript
{{ JSON.stringify({
  "audio_base64": $('Groq TTS').item.json.data.toString('base64'),
  "video_urls": [
    $('Pexels').item.json.videos[0].video_files[0].link,
    $('Pexels').item.json.videos[1].video_files[0].link,
    $('Pexels').item.json.videos[2].video_files[0].link
  ],
  "title": $('RSS Read1').item.json.title.substring(0, 50),
  "source": "Source: " + $('RSS Read1').item.json.title.split(' - ').pop()
}) }}
```

**Response Format:** File
**Download:** Yes
**File Name:** `video_{{ $now.format('yyyy-MM-dd_HHmmss') }}.mp4`

### Alternative: Using Audio URL Instead of Base64

If your audio is hosted somewhere, you can use `audio_url` instead:

```javascript
{{ JSON.stringify({
  "audio_url": $('Groq TTS').item.json.audio_url,
  "video_urls": [
    $('Pexels').item.json.videos[0].video_files[0].link,
    $('Pexels').item.json.videos[1].video_files[0].link,
    $('Pexels').item.json.videos[2].video_files[0].link
  ],
  "title": $('RSS Read1').item.json.title.substring(0, 50),
  "source": "Source: " + $('RSS Read1').item.json.title.split(' - ').pop()
}) }}
```

## Local Development

### Using Docker

```bash
# Build
docker build -t video-assembler .

# Run
docker run -p 5000:5000 video-assembler
```

### Using Python Directly

```bash
# Install dependencies
pip install -r requirements.txt

# Ensure FFmpeg is installed
# macOS: brew install ffmpeg
# Ubuntu: sudo apt-get install ffmpeg

# Run
python app.py
```

API will be available at `http://localhost:5000`

## Technical Details

### Video Processing Pipeline

1. **Audio Handling:** Download or decode base64 audio
2. **Duration Calculation:** Split audio duration equally across clips
3. **Video Download:** Fetch all video clips from provided URLs
4. **Normalization:** Resize/crop each clip to 1080x1920 vertical format
5. **Trimming:** Cut each clip to match calculated duration
6. **Concatenation:** Join all normalized clips
7. **Overlay:** Add title (white, top) and source (yellow, bottom) text
8. **Audio Mix:** Combine audio with video using `-shortest` flag
9. **Optimization:** Apply web optimization with `-movflags +faststart`

### FFmpeg Settings

- **Preset:** `ultrafast` (optimized for free tier servers)
- **CRF:** 23 (balanced quality/size)
- **Audio:** AAC at 192kbps
- **Format:** MP4 with fast-start flag for web streaming

### Performance

- **Processing Time:** ~30-90 seconds for 3 clips (depends on video length)
- **Gunicorn Timeout:** 300 seconds
- **Workers:** 1 (free tier limitation)

## Troubleshooting

### Video Processing Takes Too Long

The free tier has limited resources. To speed up:
- Use shorter video clips
- Reduce number of clips
- Ensure video URLs are fast to download

### "Failed to download video" Error

- Check that video URLs are publicly accessible
- Ensure URLs point directly to video files (not HTML pages)
- Pexels API should provide direct video file URLs

### Render Service Sleeping

Free tier services sleep after 15 minutes of inactivity:
- First request after sleep takes ~30-60 seconds to wake up
- Consider using a cron job to ping `/health` if you need faster response

## License

MIT License - feel free to use for any purpose.

## Contributing

Issues and pull requests welcome!
