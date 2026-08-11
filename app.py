import subprocess
import tempfile
import os
from flask import Flask, request, send_file, jsonify

app = Flask(__name__)

SHARED_SECRET = os.environ.get("SHARED_SECRET", "change-me")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/capture-audio", methods=["POST"])
def capture_audio():
    auth = request.headers.get("X-Secret")
    if auth != SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    hls_url = data.get("hlsUrl")
    seconds = int(data.get("seconds", 60))

    if not hls_url:
        return jsonify({"error": "hlsUrl is required"}), 400

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        output_path = tmp.name

    cmd = [
        "ffmpeg", "-y",
        "-i", hls_url,
        "-t", str(seconds),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "44100",
        "-ab", "64k",
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=seconds + 30)
        if result.returncode != 0:
            return jsonify({
                "error": "ffmpeg failed",
                "stderr": result.stderr.decode(errors="ignore")[-2000:]
            }), 500

        return send_file(output_path, mimetype="audio/mpeg", as_attachment=True, download_name="audio.mp3")
    finally:
        pass

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
