from flask import Flask, request, jsonify, Response # Added Response
from flask_cors import CORS
from src import api_get_video_info, api_download_video, sanitize_url
import os

app = Flask(__name__)
CORS(app)

TEMP_DIR = "/tmp/streamforge_cache"

@app.route('/api/formats', methods=['POST'])
def get_formats():
    data = request.json or {}
    url = sanitize_url(data.get('url'))
    if not url:
        return jsonify({"error": "Invalid URL"}), 400
    try:
        video_data = api_get_video_info(url)
        return jsonify(video_data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['POST'])
def download_video():
    data = request.json or {}
    url = sanitize_url(data.get('url'))
    format_id = data.get('format_id')
    is_audio_only = data.get('is_audio_only', False)
    
    if not url or not format_id:
        return jsonify({"error": "Missing URL or format_id"}), 400
        
    try:
        # 1. Forge the video into the hidden container cache
        filename = api_download_video(url, format_id, is_audio_only, output_dir=TEMP_DIR)
        
        return jsonify({
            "message": "Forge complete!",
            "filename": filename,
            "download_url": f"http://localhost:5000/api/fetch/{filename}"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/fetch/<filename>', methods=['GET'])
def fetch_file(filename):
    file_path = os.path.join(TEMP_DIR, filename)
    
    if not os.path.exists(file_path):
        return jsonify({"error": "File not found or already downloaded"}), 404

    # 2. Use a generator to stream the file and delete it afterward
    def generate():
        with open(file_path, 'rb') as f:
            yield from f
        
        # This line runs ONLY after the browser finishes receiving the file
        try:
            os.remove(file_path)
            print(f"CLEANUP: Deleted {filename} from cache.")
        except Exception as e:
            print(f"CLEANUP ERROR: {e}")

    return Response(
        generate(),
        mimetype='application/octet-stream',
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)