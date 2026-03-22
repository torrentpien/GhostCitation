"""GhostCitation — detect fake AI-generated references in academic papers."""

import os
import uuid
import json
import logging

from flask import Flask, render_template, request, jsonify, Response
from werkzeug.utils import secure_filename

from ghostcitation.extractor import extract_references, parse_raw_lines
from ghostcitation import checker
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
        return jsonify({"error": "No file selected"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF and DOCX files are supported"}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_name)
    file.save(file_path)

    try:
        refs = extract_references(file_path)
    except Exception as e:
        logger.exception("Failed to extract references")
        return jsonify({"error": f"Failed to parse file: {e}"}), 500
    finally:
        # Clean up uploaded file
        if os.path.exists(file_path):
            os.remove(file_path)

    if not refs:
        return jsonify({"error": "No references section found. Ensure the file contains a References or Bibliography section."}), 400

    return jsonify({
        "references": refs,
        "count": len(refs),
    })


@app.route("/parse-text", methods=["POST"])
def parse_text():
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "Missing text data"}), 400

    text = data["text"].strip()
    if not text:
        return jsonify({"error": "Empty text"}), 400

    refs = parse_raw_lines(text)
    if not refs:
        return jsonify({"error": "No references found in text"}), 400

    return jsonify({"references": refs, "count": len(refs)})


@app.route("/check", methods=["POST"])
def check():
    data = request.get_json()
    if not data or "references" not in data:
        return jsonify({"error": "Missing reference data"}), 400

    refs = data["references"]

    # Allow passing API keys from frontend
    serpapi_key = data.get("serpapi_key", "")
    if serpapi_key:
        checker.SERPAPI_KEY = serpapi_key

    scraperapi_key = data.get("scraperapi_key", "")
    if scraperapi_key:
        checker.SCRAPERAPI_KEY = scraperapi_key

    apify_key = data.get("apify_key", "")
    if apify_key:
        checker.APIFY_KEY = apify_key

    def generate():
        """Stream SSE events for real-time progress."""
        def step_cb(ref_index, source, status):
            event = {
                "type": "step",
                "ref_index": ref_index,
                "source": source,
                "status": status,
            }
            yield f"data: {json.dumps(event)}\n\n"

        # We need to collect step events and yield them
        # Since check_references is synchronous, use a queue-like approach
        events = []

        def buffered_step_cb(ref_index, source, status):
            events.append({
                "type": "step",
                "ref_index": ref_index,
                "source": source,
                "status": status,
            })

        def progress_cb(index, total, result):
            events.append({
                "type": "progress",
                "ref_index": index - 1,
                "current": index,
                "total": total,
                "result": result,
            })

        # Run check in a thread-like manner using generator
        # Since Flask streaming needs a generator, we use a different approach:
        # Check one reference at a time and yield events
        all_results = []
        delay = 1.5 if not checker.SERPAPI_KEY else 0.3

        for i, ref in enumerate(refs):
            step_events = []

            def ref_step_cb(source, status):
                step_events.append({
                    "type": "step",
                    "ref_index": i,
                    "source": source,
                    "status": status,
                })

            result = checker.check_reference(ref, step_callback=ref_step_cb)
            all_results.append(result)

            # Yield all step events for this reference
            for evt in step_events:
                yield f"data: {json.dumps(evt)}\n\n"

            # Yield the completed reference result
            progress_evt = {
                "type": "result",
                "ref_index": i,
                "current": i + 1,
                "total": len(refs),
                "result": result,
            }
            yield f"data: {json.dumps(progress_evt)}\n\n"

            if i < len(refs) - 1:
                import time
                time.sleep(delay)

        # Yield final summary
        summary = {
            "total": len(all_results),
            "verified": sum(1 for r in all_results if r["verdict"] == "verified"),
            "misattributed": sum(1 for r in all_results if r["verdict"] == "misattributed"),
            "fabricated": sum(1 for r in all_results if r["verdict"] == "fabricated"),
        }
        done_evt = {
            "type": "done",
            "summary": summary,
            "results": all_results,
        }
        yield f"data: {json.dumps(done_evt)}\n\n"

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
