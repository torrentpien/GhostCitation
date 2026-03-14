"""GhostCitation — detect fake AI-generated references in academic papers."""

import os
import uuid
import json
import logging

from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename

from ghostcitation.extractor import extract_references
from ghostcitation.checker import check_references

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ghostcitation-dev-key")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"pdf", "docx"}
MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MB
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "未選擇檔案"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "未選擇檔案"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "僅支援 PDF 和 DOCX 檔案"}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        refs = extract_references(file_path)
    except Exception as e:
        logger.exception("Failed to extract references")
        return jsonify({"error": f"無法解析檔案: {e}"}), 500
    finally:
        # Clean up uploaded file
        if os.path.exists(file_path):
            os.remove(file_path)

    if not refs:
        return jsonify({"error": "找不到參考文獻區段，請確認檔案包含 References 或參考文獻章節"}), 400

    return jsonify({
        "references": refs,
        "count": len(refs),
    })


@app.route("/check", methods=["POST"])
def check():
    data = request.get_json()
    if not data or "references" not in data:
        return jsonify({"error": "缺少參考文獻資料"}), 400

    refs = data["references"]
    results = check_references(refs)

    summary = {
        "total": len(results),
        "verified": sum(1 for r in results if r["verdict"] == "verified"),
        "suspicious": sum(1 for r in results if r["verdict"] == "suspicious"),
        "not_found": sum(1 for r in results if r["verdict"] == "not_found"),
    }

    return jsonify({
        "results": results,
        "summary": summary,
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
