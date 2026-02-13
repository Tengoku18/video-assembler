#!/bin/bash

# Simple test script for local development
# Make sure the server is running before executing this

echo "Testing Video Assembler API..."
echo ""

# Test health endpoint
echo "1. Testing /health endpoint..."
curl -s http://localhost:5000/health | python3 -m json.tool
echo ""
echo ""

# Test assemble endpoint with sample data
echo "2. Testing /assemble endpoint..."
echo "Note: This requires valid video URLs. Update the URLs below with actual video links."
echo ""

# Example using audio URL (replace with actual URLs)
curl -X POST http://localhost:5000/assemble \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    "video_urls": [
      "https://sample-videos.com/video321/mp4/720/big_buck_bunny_720p_1mb.mp4"
    ],
    "title": "Test Video",
    "source": "Source: Test"
  }' \
  --output test_output.mp4

if [ -f test_output.mp4 ]; then
    echo ""
    echo "✅ Success! Video saved as test_output.mp4"
    echo "File size: $(ls -lh test_output.mp4 | awk '{print $5}')"
else
    echo ""
    echo "❌ Failed to generate video"
fi
