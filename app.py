import subprocess
import tempfile
import os
import base64
import traceback
from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageDraw

app = Flask(__name__)

SHARED_SECRET = os.environ.get("SHARED_SECRET", "change-me")

CAMERA_LINES = {
    "8248BBEPBV1AFE1": {"x1": 0.53, "y1": 0.22, "x2": 1.0, "y2": 0.242},
    "44245BHPSF5CF18": {"x1": 0.75, "y1": 0.396, "x2": 1.0, "y2": 0.381},
}

# Калиброванные зоны presence-check (доли 0.0-1.0), для отладочной визуализации
PRESENCE_ZONES = {
    "9C05B61PAZ3F29E": {"admin_polygon": [[0.4265,0.9987],[0.0091,0.9785],[0.0091,0.5262],[0.0326,0.4953],[0.4424,0.8466]], "excl": None},  # Tengiz (polygon, not rectangle)
    "7B03CEFPAZ84F7A": {"admin_polygon": [[0.9212,0.8513],[0.5364,0.9695],[0.2455,0.9670],[0.2682,0.7586],[0.4371,0.7116]], "excl": None},  # Kunaeva (polygon, not rectangle)
    "9C05B61PAZ246AF": {"admin_polygon": [[0.1136,0.3957],[0.2242,0.5276],[0.1371,0.8264],[0.0848,0.8345],[0.0,0.5935]], "excl": None},  # TolebiBar (new camera + new polygon)
    "7B03CEFPAZ74EC0": {"admin": {"x1":0.0,   "x2":0.4962,"y1":0.6125,"y2":0.9619}, "excl": None},                 # Baraeva
    "8248BBEPBV1AFE1": {"admin_polygon": [[0.6508,0.0],[0.9992,0.0],[0.9992,0.5128],[0.6508,0.5128]], "excl": None},  # Nazarbaeva (simplified, no exclusion)
    "9C05B61PAZ17447": {"admin": {"x1":0.1333,"x2":0.5773,"y1":0.6878,"y2":0.9987}, "excl": None},                 # Karaganda
}

LINE_COLOR = (255, 0, 0)
LINE_WIDTH = 6

ADMIN_ZONE_COLOR = (0, 200, 0)
EXCL_ZONE_COLOR = (255, 140, 0)
PERSON_BOX_COLOR = (0, 120, 255)
ANCHOR_POINT_COLOR = (255, 0, 255)


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


@app.route("/draw-debug-boxes", methods=["POST"])
def draw_debug_boxes():
    """Отладочный endpoint: рисует на кадре обнаруженные Gemini рамки людей (box_2d, шкала 0-1000),
    точку опоры каждого, и калиброванные зоны presence-check (админ зона зелёным, зона-исключение оранжевым).
    Принимает JSON: { imageBase64, did, people: [{box_2d:[y_min,x_min,y_max,x_max]}, ...] }
    Возвращает: аннотированный JPEG.
    """
    auth = request.headers.get("X-Secret")
    if auth != SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    image_b64 = data.get("imageBase64")
    did = data.get("did")
    people = data.get("people", [])
    anchor_fraction = float(data.get("anchorFraction", 0.575))

    if not image_b64:
        return jsonify({"error": "imageBase64 is required"}), 400

    try:
        img_bytes = base64.b64decode(image_b64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        img = Image.open(tmp_path).convert("RGB")
        w, h = img.size
        draw = ImageDraw.Draw(img)

        zones = PRESENCE_ZONES.get(did)
        if zones:
            if "admin_polygon" in zones:
                poly_px = [(px * w, py * h) for px, py in zones["admin_polygon"]]
                # рисуем контур вручную линиями - draw.polygon(width=) не во всех версиях Pillow
                for i in range(len(poly_px)):
                    p1 = poly_px[i]
                    p2 = poly_px[(i + 1) % len(poly_px)]
                    draw.line([p1, p2], fill=ADMIN_ZONE_COLOR, width=4)
            elif "admin" in zones:
                admin = zones["admin"]
                draw.rectangle(
                    [admin["x1"] * w, admin["y1"] * h, admin["x2"] * w, admin["y2"] * h],
                    outline=ADMIN_ZONE_COLOR, width=4
                )
            if zones.get("excl"):
                excl = zones["excl"]
                draw.rectangle(
                    [excl["x1"] * w, excl["y1"] * h, excl["x2"] * w, excl["y2"] * h],
                    outline=EXCL_ZONE_COLOR, width=4
                )

        for i, person in enumerate(people):
            box = person.get("box_2d")
            if not box or len(box) != 4:
                continue
            y_min, x_min, y_max, x_max = [v / 1000.0 for v in box]
            draw.rectangle(
                [x_min * w, y_min * h, x_max * w, y_max * h],
                outline=PERSON_BOX_COLOR, width=3
            )
            # Точку опоры больше не рисуем: реальное решение везде считается через
            # % площади пересечения рамки с зоной, а не через одну точку. Точка тут
            # раньше не совпадала с реальной логикой и только вводила в заблуждение.
            draw.text((x_min * w + 4, y_min * h + 4), f"#{i+1}", fill=PERSON_BOX_COLOR)

        img.save(tmp_path, "JPEG", quality=95)
        return send_file(tmp_path, mimetype="image/jpeg", as_attachment=True, download_name="debug_boxes.jpg")

    except Exception as e:
        print(f"[draw-debug-boxes] EXCEPTION: {traceback.format_exc()}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route("/draw-zone-only", methods=["POST"])
def draw_zone_only():
    """Рисует ТОЛЬКО обводку калиброванной зоны (жёлтая, толстая линия, без заливки) на кадре -
    без рамок людей, без точек. Используется, чтобы затем спросить Gemini напрямую: какой процент
    ЭТОЙ обведённой области занимает тело человека - вместо поиска людей по всему кадру.
    Принимает JSON: { imageBase64, did }. Возвращает: аннотированный JPEG.
    """
    auth = request.headers.get("X-Secret")
    if auth != SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    image_b64 = data.get("imageBase64")
    did = data.get("did")

    if not image_b64:
        return jsonify({"error": "imageBase64 is required"}), 400

    try:
        img_bytes = base64.b64decode(image_b64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        img = Image.open(tmp_path).convert("RGB")
        w, h = img.size
        draw = ImageDraw.Draw(img)

        ZONE_OUTLINE_COLOR = (255, 220, 0)  # ярко-жёлтый, толстая линия
        ZONE_OUTLINE_WIDTH = 6

        zones = PRESENCE_ZONES.get(did)
        if zones:
            if "admin_polygon" in zones:
                poly_px = [(px * w, py * h) for px, py in zones["admin_polygon"]]
                for i in range(len(poly_px)):
                    p1 = poly_px[i]
                    p2 = poly_px[(i + 1) % len(poly_px)]
                    draw.line([p1, p2], fill=ZONE_OUTLINE_COLOR, width=ZONE_OUTLINE_WIDTH)
            elif "admin" in zones:
                admin = zones["admin"]
                draw.rectangle(
                    [admin["x1"] * w, admin["y1"] * h, admin["x2"] * w, admin["y2"] * h],
                    outline=ZONE_OUTLINE_COLOR, width=ZONE_OUTLINE_WIDTH
                )

        img.save(tmp_path, "JPEG", quality=95)
        return send_file(tmp_path, mimetype="image/jpeg", as_attachment=True, download_name="zone_only.jpg")

    except Exception as e:
        print(f"[draw-zone-only] EXCEPTION: {traceback.format_exc()}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route("/crop-zone", methods=["POST"])
def crop_zone():
    """Вырезает из кадра только калиброванную зону администратора (кодом, без ИИ).
    Для полигона - дополнительно закрашивает чёрным всё внутри bounding box, что снаружи
    самого многоугольника, чтобы Gemini физически не видела ничего за пределами настоящей зоны.
    Принимает: { imageBase64, did }. Возвращает: обрезанный JPEG.
    """
    auth = request.headers.get("X-Secret")
    if auth != SHARED_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(force=True)
    image_b64 = data.get("imageBase64")
    did = data.get("did")

    if not image_b64:
        return jsonify({"error": "imageBase64 is required"}), 400

    zones = PRESENCE_ZONES.get(did)
    if not zones:
        return jsonify({"error": f"no zone configured for did={did!r}, known dids={list(PRESENCE_ZONES.keys())}"}), 400

    try:
        img_bytes = base64.b64decode(image_b64)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name

        img = Image.open(tmp_path).convert("RGB")
        w, h = img.size

        if "admin_polygon" in zones:
            poly_px = [(px * w, py * h) for px, py in zones["admin_polygon"]]
            xs = [p[0] for p in poly_px]
            ys = [p[1] for p in poly_px]
            x1, x2 = max(0, min(xs)), min(w, max(xs))
            y1, y2 = max(0, min(ys)), min(h, max(ys))

            mask = Image.new("L", (w, h), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.polygon(poly_px, fill=255)
            black_bg = Image.new("RGB", (w, h), (0, 0, 0))
            img = Image.composite(img, black_bg, mask)

            cropped = img.crop((int(x1), int(y1), int(x2), int(y2)))
        elif "admin" in zones:
            admin = zones["admin"]
            x1, y1, x2, y2 = admin["x1"] * w, admin["y1"] * h, admin["x2"] * w, admin["y2"] * h
            cropped = img.crop((int(x1), int(y1), int(x2), int(y2)))
        else:
            return jsonify({"error": f"zone for did={did!r} has neither admin_polygon nor admin"}), 500

        if cropped.width < 2 or cropped.height < 2:
            return jsonify({"error": "cropped region too small", "size": [cropped.width, cropped.height]}), 500

        cropped.save(tmp_path, "JPEG", quality=95)
        return send_file(tmp_path, mimetype="image/jpeg", as_attachment=True, download_name="cropped.jpg")

    except Exception as e:
        print(f"[crop-zone] EXCEPTION: {traceback.format_exc()}", flush=True)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
