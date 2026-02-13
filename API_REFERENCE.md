# API Reference

## Base URL
```
Production: https://your-app.onrender.com
Local: http://localhost:5000
```

---

## Endpoints

### 1. Health Check

**GET /health**

Check if the service is running and FFmpeg is available.

**Response:**
```json
{
  "status": "healthy",
  "service": "video-assembler",
  "ffmpeg_available": true
}
```

**Status Codes:**
- `200` - Service is healthy

**Example:**
```bash
curl https://your-app.onrender.com/health
```

---

### 2. Assemble Video

**POST /assemble**

Assemble a vertical video from audio and video clips.

#### Request Body

```json
{
  "audio_base64": "string (optional)",
  "audio_url": "string (optional)",
  "video_urls": ["string"],
  "title": "string (optional)",
  "source": "string (optional)"
}
```

#### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `audio_base64` | string | No* | Base64-encoded audio file (e.g., from Groq TTS) |
| `audio_url` | string | No* | Direct URL to audio file (mp3, wav, etc.) |
| `video_urls` | array | Yes | Array of 1-5 video URLs (mp4 format recommended) |
| `title` | string | No | Text to display at top (default: "Video Title") |
| `source` | string | No | Text to display at bottom (default: "Source") |

*Either `audio_base64` OR `audio_url` is required

#### Response

**Success (200):**
- Returns MP4 file as download
- Content-Type: `video/mp4`
- Filename: `assembled_video.mp4`

**Error (400/500):**
```json
{
  "error": "Error message description"
}
```

#### Status Codes

| Code | Meaning |
|------|---------|
| `200` | Success - video file returned |
| `400` | Bad request - invalid parameters |
| `500` | Server error - processing failed |

#### Example Requests

**Using audio_url:**
```bash
curl -X POST https://your-app.onrender.com/assemble \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/audio.mp3",
    "video_urls": [
      "https://example.com/video1.mp4",
      "https://example.com/video2.mp4",
      "https://example.com/video3.mp4"
    ],
    "title": "Amazing Nature Video",
    "source": "Source: Pexels"
  }' \
  --output final_video.mp4
```

**Using audio_base64:**
```bash
# First, encode audio
AUDIO_BASE64=$(cat audio.mp3 | base64)

# Then send request
curl -X POST https://your-app.onrender.com/assemble \
  -H "Content-Type: application/json" \
  -d "{
    \"audio_base64\": \"$AUDIO_BASE64\",
    \"video_urls\": [
      \"https://example.com/video1.mp4\",
      \"https://example.com/video2.mp4\"
    ],
    \"title\": \"Tech Tutorial\",
    \"source\": \"Source: YouTube\"
  }" \
  --output output.mp4
```

**Python Example:**
```python
import requests
import base64

# Option 1: Using audio file
with open('audio.mp3', 'rb') as f:
    audio_b64 = base64.b64encode(f.read()).decode()

response = requests.post(
    'https://your-app.onrender.com/assemble',
    json={
        'audio_base64': audio_b64,
        'video_urls': [
            'https://example.com/clip1.mp4',
            'https://example.com/clip2.mp4',
            'https://example.com/clip3.mp4'
        ],
        'title': 'My Video',
        'source': 'Source: My Channel'
    }
)

if response.status_code == 200:
    with open('output.mp4', 'wb') as f:
        f.write(response.content)
    print('Video saved!')
else:
    print('Error:', response.json()['error'])

# Option 2: Using audio URL
response = requests.post(
    'https://your-app.onrender.com/assemble',
    json={
        'audio_url': 'https://example.com/audio.mp3',
        'video_urls': ['https://example.com/video.mp4'],
        'title': 'Quick Video',
        'source': 'Source: Test'
    }
)
```

**JavaScript/Node.js Example:**
```javascript
const axios = require('axios');
const fs = require('fs');

async function assembleVideo() {
  try {
    // Using audio URL
    const response = await axios.post(
      'https://your-app.onrender.com/assemble',
      {
        audio_url: 'https://example.com/audio.mp3',
        video_urls: [
          'https://example.com/video1.mp4',
          'https://example.com/video2.mp4'
        ],
        title: 'My Video Title',
        source: 'Source: Example'
      },
      {
        responseType: 'arraybuffer'
      }
    );

    fs.writeFileSync('output.mp4', response.data);
    console.log('Video saved!');
  } catch (error) {
    console.error('Error:', error.response.data);
  }
}

assembleVideo();
```

---

## Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `Invalid JSON` | Malformed request body | Check JSON syntax |
| `video_urls array is required` | Missing video_urls | Include at least 1 video URL |
| `Either audio_base64 or audio_url is required` | No audio provided | Include audio_base64 OR audio_url |
| `Maximum 5 video clips allowed` | Too many videos | Reduce to 5 or fewer clips |
| `At least one video URL is required` | Empty video_urls | Include at least 1 URL |
| `Failed to decode audio_base64` | Invalid base64 | Check base64 encoding |
| `Failed to download audio file` | Invalid audio URL | Verify URL is accessible |
| `Failed to determine audio duration` | Corrupted audio file | Use valid audio file |
| `Failed to download video N` | Video URL inaccessible | Check video URLs |
| `Failed to normalize video N` | Video processing error | Try different video format |
| `Failed to concatenate videos` | FFmpeg concatenation error | Check video compatibility |
| `Failed to create final video` | Final processing error | Check all inputs |

---

## Rate Limits

**Render.com Free Tier:**
- No explicit rate limit
- Concurrent requests limited by single worker
- Service sleeps after 15 minutes of inactivity
- First request after sleep: 30-60s delay

**Recommendations:**
- Queue requests (don't send simultaneously)
- Keep audio under 60 seconds for faster processing
- Use 2-5 video clips for optimal performance

---

## Video Specifications

### Input Requirements

**Audio:**
- Formats: MP3, WAV, AAC, OGG
- Max size: No hard limit (but larger = slower)
- Recommended: < 5 MB, < 60 seconds

**Video:**
- Formats: MP4, MOV, AVI, MKV
- Max clips: 5
- Recommended: MP4, H.264 codec
- Any resolution (will be normalized)

### Output Format

- **Container:** MP4
- **Video Codec:** H.264
- **Audio Codec:** AAC
- **Resolution:** 1080x1920 (vertical)
- **Frame Rate:** Matches source (typically 24-30 fps)
- **Audio Bitrate:** 192 kbps
- **Video Quality:** CRF 23
- **Optimization:** Fast-start enabled

### Text Overlays

**Title (Top):**
- Font: DejaVu Sans Bold
- Size: 48px
- Color: White
- Border: 3px black
- Position: Top center, 50px from top

**Source (Bottom):**
- Font: DejaVu Sans
- Size: 36px
- Color: Yellow
- Border: 3px black
- Position: Bottom center, 100px from bottom

---

## Processing Time Estimates

| Clips | Audio Duration | Estimated Time |
|-------|----------------|----------------|
| 1 | 15-30s | 20-40s |
| 2 | 30-45s | 30-60s |
| 3 | 45-60s | 40-90s |
| 5 | 60-90s | 60-120s |

*Times vary based on video sizes, network speed, and server load*

---

## Best Practices

### 1. Audio
- Keep under 60 seconds for faster processing
- Use MP3 format for best compatibility
- 128-192 kbps bitrate is sufficient

### 2. Videos
- Use MP4 format with H.264 codec
- Prefer vertical (portrait) videos when possible
- Keep file sizes reasonable (< 50 MB each)
- Ensure URLs are direct links (not web pages)

### 3. Text
- Keep titles under 50 characters
- Avoid special characters that need escaping
- Use clear, readable text

### 4. Performance
- Send requests sequentially (not in parallel)
- Ping /health before sending large requests
- Handle 30-60s wake-up delay after service sleep

### 5. Error Handling
- Always check response status code
- Log error messages for debugging
- Retry failed requests with exponential backoff

---

## Support

**Documentation:**
- [README.md](README.md) - Complete guide
- [QUICK_START.md](QUICK_START.md) - Quick deployment
- [PROJECT_SUMMARY.md](PROJECT_SUMMARY.md) - Overview
- [examples.json](examples.json) - Sample requests

**Testing:**
- Use [test_local.sh](test_local.sh) for local testing
- See [examples.json](examples.json) for working URLs

**Issues:**
- Report bugs on GitHub
- Include error messages and request body
