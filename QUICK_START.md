# Quick Start Guide

## 🚀 Deploy to Render.com (5 minutes)

### Step 1: Push to GitHub
```bash
cd video-assembler
git init
git add .
git commit -m "Initial commit: Video Assembler API"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/video-assembler.git
git push -u origin main
```

### Step 2: Deploy on Render
1. Go to https://dashboard.render.com/
2. Click **"New +"** → **"Blueprint"**
3. Connect your GitHub repository
4. Render detects `render.yaml` automatically
5. Click **"Apply"**
6. Wait 3-5 minutes for deployment

### Step 3: Get Your API URL
After deployment, your API URL will be:
```
https://video-assembler-XXXX.onrender.com
```

### Step 4: Test It
```bash
# Test health
curl https://video-assembler-XXXX.onrender.com/health

# Test video assembly
curl -X POST https://video-assembler-XXXX.onrender.com/assemble \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "YOUR_AUDIO_URL",
    "video_urls": ["VIDEO_URL_1", "VIDEO_URL_2"],
    "title": "My Video",
    "source": "Source: Test"
  }' \
  --output final.mp4
```

## 🔧 Local Development

### Using Docker (Recommended)
```bash
docker build -t video-assembler .
docker run -p 5000:5000 video-assembler
```

### Using Python
```bash
pip install -r requirements.txt
python app.py
```

Then test with:
```bash
./test_local.sh
```

## 🔗 n8n Integration

### HTTP Request Node Settings
- **Method:** POST
- **URL:** `https://video-assembler-XXXX.onrender.com/assemble`
- **Body:** JSON
- **Response Format:** File

### Expression for Body:
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

## 📝 Common Issues

### Issue: Service is sleeping
**Solution:** Free tier sleeps after 15 min. First request wakes it (30-60s delay).

### Issue: Video processing timeout
**Solution:** Use shorter clips or fewer videos. Free tier has limited CPU.

### Issue: Can't download videos
**Solution:** Ensure URLs are direct links to video files, not web pages.

## 🎯 What Gets Created

Your video will be:
- **Format:** MP4 (1080x1920 vertical)
- **Title:** White text at top with black border
- **Source:** Yellow text at bottom with black border
- **Audio:** Mixed from your input
- **Length:** Matches audio duration (uses `-shortest` flag)
- **Optimized:** Fast-start enabled for web playback

## 💡 Pro Tips

1. **Pexels Videos:** Use HD quality, vertical preferred
2. **Audio:** Keep under 60 seconds for faster processing
3. **Videos:** 2-5 clips work best (1 clip = simple, 5 = max)
4. **Text:** Keep title under 50 characters for readability
5. **Testing:** Use /health endpoint to check if service is awake

## 📊 API Response Times

- **Health Check:** < 1 second
- **Video Assembly:**
  - 1 clip: ~20-40 seconds
  - 3 clips: ~40-90 seconds
  - 5 clips: ~60-120 seconds

*Times vary based on video sizes and download speeds*
