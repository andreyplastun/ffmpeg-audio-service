import subprocess
import tempfile
import os
import traceback
from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw

app = Flask(__name__)

SHARED_SECRET = os.environ.get("SHARED_SECRET", "change-me")

CAMERA_LINES = {
    "8248BBEPBV1AFE1": {"x1": 0.53, "y1": 0.006, "x2": 1.0, "y2": 0.213},
    "44245BHPSF5CF18": {"x1": 0.68, "y1": 0.18, "x2": 1.0, "y2": 0.269},
}

LINE_COLOR = (255, 0, 0)
LINE_WIDTH = 6


def draw_boundary_line(image_path, did):
    """Рисует красную линию-разделитель. Возвращает (drawn: bool, debug_message: str)."""
    line = CAMERA_LINES.get(did)
    if not line:
        return False, f"no line configured for did={did!r}, known dids={list(CAMERA_LINES.keys())}"

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    x1 = line["x1"] * w
    y1 = line["y1"] * h
    x2 = line["x2"] * w
    y2 = line["y2"] * h

    draw.line([(x1, y1), (x2, y2)], fill=LINE_COLOR, width=LINE_WIDTH)
    img.save(image_path, "JPEG", quality=95)
    return True, f"drawn ok, size={w}x{h}, line=({x1:.0f},{y1:.0f})-({x2:.0f},{y2:.0f})"


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


@app.route("/capture-frame", methods=["POST"])
def capture_frame():
    auth = request.headers.get("X-Secret")
    if auth != SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    hls_url = data.get("hlsUrl")
    did = data.get("did")

    print(f"[capture-frame] did received: {did!r}", flush=True)

    if not hls_url:
        return jsonify({"error": "hlsUrl is required"}), 400

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        output_path = tmp.name

    cmd = [
        "ffmpeg", "-y",
        "-i", hls_url,
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ]

    debug_header = "did-missing"

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 500:
            return jsonify({
                "error": "ffmpeg failed to capture frame",
                "stderr": result.stderr.decode(errors="ignore")[-2000:]
            }), 500

        if did:
            try:
                drawn, msg = draw_boundary_line(output_path, did)
                debug_header = f"drawn={drawn}; {msg}"
                print(f"[capture-frame] draw_boundary_line result: {debug_header}", flush=True)
            except Exception as draw_err:
                debug_header = f"EXCEPTION: {draw_err}"
                print(f"[capture-frame] draw_boundary_line EXCEPTION: {traceback.format_exc()}", flush=True)

        resp = send_file(output_path, mimetype="image/jpeg", as_attachment=True, download_name="frame.jpg")
        resp.headers["X-Line-Debug"] = debug_header[:200]
        return resp
    finally:
        pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
