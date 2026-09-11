import subprocess
import tempfile
import os
from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw

app = Flask(__name__)

SHARED_SECRET = os.environ.get("SHARED_SECRET", "change-me")

# Линия-разделитель "админская сторона / клиентская сторона" для конкретных камер.
# Координаты в ДОЛЯХ ширины/высоты кадра (0.0-1.0), а не в жёстких пикселях -
# так линия не съезжает, даже если реальное разрешение кадра отличается от того,
# на чём эти координаты подбирались.
#
# ЧЕРНОВЫЕ значения, подобраны по присланным скриншотам - ПРОВЕРИТЬ ВИЗУАЛЬНО
# после деплоя на реальном кадре с /capture-frame, поправить при необходимости.
CAMERA_LINES = {
    # Назарбаева
    "8248BBEPBV1AFE1": {"x1": 0.53, "y1": 0.006, "x2": 1.0, "y2": 0.213},
    # Толе Би (регламент-камера тоже может быть той же зоны - используем тот же did, что реально шлёт HLS)
    "44245BHPSF5CF18": {"x1": 0.68, "y1": 0.18, "x2": 1.0, "y2": 0.269},
}

LINE_COLOR = (255, 0, 0)  # красный
LINE_WIDTH = 6


def draw_boundary_line(image_path, did):
    """Рисует красную линию-разделитель на кадре, если для этой камеры она задана."""
    line = CAMERA_LINES.get(did)
    if not line:
        return  # для этой камеры оверлей не настроен - оставляем кадр как есть

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img)

    x1 = line["x1"] * w
    y1 = line["y1"] * h
    x2 = line["x2"] * w
    y2 = line["y2"] * h

    draw.line([(x1, y1), (x2, y2)], fill=LINE_COLOR, width=LINE_WIDTH)
    img.save(image_path, "JPEG", quality=95)


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
    did = data.get("did")  # опционально: id камеры, чтобы знать, нужно ли рисовать линию

    if not hls_url:
        return jsonify({"error": "hlsUrl is required"}), 400

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        output_path = tmp.name

    # Забираем ровно один кадр из живого потока
    cmd = [
        "ffmpeg", "-y",
        "-i", hls_url,
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) < 500:
            return jsonify({
                "error": "ffmpeg failed to capture frame",
                "stderr": result.stderr.decode(errors="ignore")[-2000:]
            }), 500

        if did:
            try:
                draw_boundary_line(output_path, did)
            except Exception as draw_err:
                # если рисование сломалось - не роняем весь запрос, отдаём кадр как есть
                pass

        return send_file(output_path, mimetype="image/jpeg", as_attachment=True, download_name="frame.jpg")
    finally:
        pass


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
