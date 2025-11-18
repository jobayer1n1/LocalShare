import os
import argparse
import signal
import sys
import shutil
# Added Response and mimetypes for streaming support
from flask import Flask, request, send_file, render_template_string, redirect, url_for, abort, session, jsonify, Response
import time
from datetime import datetime
import subprocess
from werkzeug.utils import secure_filename
import zipfile
import io
import urllib.request
import re
import mimetypes
import tempfile
import threading

app = None
shared_dir = ""
connected_ips = set()
upload_sessions = {}  # Track active uploads for cancellation
upload_lock = threading.Lock()

# ---------- UPDATE FEATURE ----------
SCRIPT_URL = "https://raw.githubusercontent.com/jobayer1n1/LocalShare/main/LocalShare.py"

def update_script():
    print("Downloading latest version from GitHub...")
    try:
        script_path = os.path.abspath(sys.argv[0])
        backup_path = script_path + ".backup"
        
        # Create backup
        shutil.copy2(script_path, backup_path)
        print(f"Backup created: {backup_path}")
        
        # Download new version
        print(f"Downloading from: {SCRIPT_URL}")
        with urllib.request.urlopen(SCRIPT_URL) as response:
            if response.status != 200:
                print(f"Download failed with status: {response.status}")
                sys.exit(1)
            
            new_content = response.read()
            
        # Write new version
        with open(script_path, 'wb') as f:
            f.write(new_content)
        
        print("✓ Updated successfully!")
        print(f"Backup saved as: {backup_path}")
        print("Please restart the script to use the new version.")
        sys.exit(0)
        
    except urllib.error.URLError as e:
        print(f"Network error: {e}")
        print("Make sure you have internet connection and the URL is correct.")
        sys.exit(1)
    except Exception as e:
        print(f"Update failed: {e}")
        # Restore backup if it exists
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, script_path)
            print("Restored from backup.")
        sys.exit(1)
# ------------------------------------


def cleanup_shared_files():
    global shared_dir
    if shared_dir and os.path.exists(shared_dir):
        print(f"\nCleaning up shared files in: {shared_dir}")
        try:
            for filename in os.listdir(shared_dir):
                file_path = os.path.join(shared_dir, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)
                        print(f"Deleted: {filename}")
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                        print(f"Deleted directory: {filename}")
                except Exception as e:
                    print(f"Error deleting {file_path}: {e}")
            print("Cleanup completed successfully!")
        except Exception as e:
            print(f"Error during cleanup: {e}")

def signal_handler(signum, frame):
    print(f"\nReceived signal {signum}. Shutting down server...")
    cleanup_shared_files()
    sys.exit(0)

def register_signal_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    try:
        signal.signal(signal.SIGTERM, signal_handler)
    except:
        pass


class FileInfo:
    def __init__(self, path, base_dir):
        self.path = path
        self.name = os.path.basename(path)
        self.relpath = os.path.relpath(path, base_dir)
        self.is_dir = os.path.isdir(path)
        stat = os.stat(path)
        self.size = stat.st_size if not self.is_dir else self._get_dir_size(path)
        self.mtime = stat.st_mtime
        self.ext = os.path.splitext(self.name)[1].lower()
        
    def _get_dir_size(self, path):
        total = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    if os.path.exists(fp):
                        total += os.path.getsize(fp)
        except:
            pass
        return total
    
    @property
    def is_video(self):
        return self.ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v']
    
    @property
    def is_audio(self):
        return self.ext in ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma', '.opus']
    
    @property
    def is_image(self):
        return self.ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico']
    
    @property
    def is_subtitle(self):
        return self.ext in ['.srt', '.vtt']
    
    @property
    def can_stream(self):
        return self.is_video or self.is_audio or self.is_image
        
    @property
    def size_h(self):
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    @property
    def mtime_h(self):
        return datetime.fromtimestamp(self.mtime).strftime('%Y-%m-%d %H:%M:%S')


def build_app(base_dir, allow_delete=False, pin=None):
    global app, shared_dir
    shared_dir = base_dir
    
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    app.config['BASE_DIR'] = base_dir
    app.config['ALLOW_DELETE'] = allow_delete
    app.config['PIN'] = pin
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024
    
    def check_auth():
        if app.config['PIN']:
            return session.get('authenticated') == True
        return True
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if not app.config['PIN']:
            return redirect(url_for('index'))
            
        if request.method == 'POST':
            if request.form.get('pin') == app.config['PIN']:
                session['authenticated'] = True
                return redirect(url_for('index'))
            else:
                return render_template_string(LOGIN_TEMPLATE, error=True)
        
        return render_template_string(LOGIN_TEMPLATE, error=False)
    
    @app.route('/logout')
    def logout():
        session.pop('authenticated', None)
        return redirect(url_for('login'))
    
    @app.before_request
    def track_visitor():
        if request.endpoint and request.endpoint != 'static':
            ip = request.remote_addr
            if ip:
                connected_ips.add(ip)
    
    @app.route('/stats')
    def stats():
        if not check_auth():
            return jsonify({'error': 'Unauthorized'}), 403
        return jsonify({'connected_users': len(connected_ips)})
    
    @app.route('/')
    def index():
        if not check_auth():
            return redirect(url_for('login'))
            
        files = []
        for item in os.listdir(base_dir):
            full_path = os.path.join(base_dir, item)
            files.append(FileInfo(full_path, base_dir))
        files.sort(key=lambda x: x.mtime, reverse=True)
        
        return render_template_string(HTML_TEMPLATE, 
                                   files=files, 
                                   allow_delete=allow_delete,
                                   pin_required=pin is not None)

    @app.route('/files/<path:filename>')
    def files(filename):
        if not check_auth():
            return redirect(url_for('login'))
            
        full_path = os.path.join(base_dir, filename)
        if not os.path.exists(full_path):
            abort(404)
            
        if os.path.isdir(full_path):
            memory_file = io.BytesIO()
            with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files_list in os.walk(full_path):
                    for file in files_list:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, full_path)
                        zf.write(file_path, arcname)
            memory_file.seek(0)
            return send_file(memory_file, 
                           download_name=f"{filename}.zip",
                           as_attachment=True,
                           mimetype='application/zip')
        
        return send_file(full_path, as_attachment=True)
    
    @app.route('/stream/<path:filename>')
    def stream(filename):
        if not check_auth():
            return redirect(url_for('login'))
            
        full_path = os.path.join(base_dir, filename)
        if not os.path.exists(full_path):
            abort(404)
        
        ext = os.path.splitext(filename)[1].lower()
        
        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico']:
            return render_template_string(IMAGE_VIEWER_TEMPLATE, 
                                         filename=filename,
                                         file_url=url_for('view_file', filename=filename))
        
        elif ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v']:
            subtitles = []
            for item in os.listdir(base_dir):
                if item.lower().endswith(('.vtt', '.srt')):
                    subtitles.append(item)
            subtitles.sort()
            
            return render_template_string(VIDEO_PLAYER_TEMPLATE, 
                                         filename=filename,
                                         file_url=url_for('view_file', filename=filename),
                                         subtitles=subtitles)
        
        elif ext in ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma', '.opus']:
            return render_template_string(AUDIO_PLAYER_TEMPLATE, 
                                         filename=filename,
                                         file_url=url_for('view_file', filename=filename))
        
        else:
            return "File type not supported for streaming", 400
    
    @app.route('/view/<path:filename>')
    def view_file(filename):
        if not check_auth():
            return redirect(url_for('login'))
            
        full_path = os.path.join(base_dir, filename)
        if not os.path.exists(full_path):
            abort(404)
        
        # Check if it's a media file that requires range support (streaming)
        ext = os.path.splitext(filename)[1].lower()
        is_media = ext in ['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v',
                           '.mp3', '.wav', '.m4a', '.flac', '.aac', '.wma', '.opus']

        if is_media:
            file_size = os.path.getsize(full_path)
            range_header = request.headers.get('Range', None)
            
            # Get the appropriate MIME type
            mime_type = mimetypes.guess_type(full_path)[0]
            if not mime_type:
                mime_type = 'application/octet-stream'
            
            if not range_header:
                # No range request - send whole file with proper headers for streaming
                def generate():
                    with open(full_path, 'rb') as f:
                        while True:
                            chunk = f.read(8192)  # 8KB chunks
                            if not chunk:
                                break
                            yield chunk
                
                rv = Response(generate(), 200, mimetype=mime_type, direct_passthrough=True)
                rv.headers.add('Content-Length', str(file_size))
                rv.headers.add('Accept-Ranges', 'bytes')
                return rv
            
            # Parse the Range header (e.g., "bytes=0-", "bytes=1024-2048")
            m = re.search(r'bytes=(\d+)-(\d*)', range_header)
            if not m:
                abort(416)  # Range Not Satisfiable
                
            g = m.groups()
            byte_start = int(g[0])
            byte_end = int(g[1]) if g[1] else file_size - 1
            
            # Validate range
            if byte_start >= file_size or byte_end >= file_size or byte_start > byte_end:
                abort(416)
            
            length = byte_end - byte_start + 1
            
            # Read and send the requested range
            def generate_range():
                with open(full_path, 'rb') as f:
                    f.seek(byte_start)
                    remaining = length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)  # 8KB chunks
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            
            # Construct Partial Content Response (206)
            rv = Response(generate_range(), 206, mimetype=mime_type, direct_passthrough=True)
            rv.headers.add('Content-Range', f'bytes {byte_start}-{byte_end}/{file_size}')
            rv.headers.add('Accept-Ranges', 'bytes')
            rv.headers.add('Content-Length', str(length))
            return rv

        # For images/other files, standard send is fine
        return send_file(full_path)

    @app.route('/upload', methods=['POST'])
    def upload_file():
        if not check_auth():
            return jsonify({'error': 'Unauthorized'}), 403
        
        # Generate unique session ID for this upload
        session_id = request.headers.get('X-Upload-Session-ID')
        if not session_id:
            return jsonify({'error': 'No session ID provided'}), 400
        
        # Check if upload was cancelled
        with upload_lock:
            if session_id in upload_sessions and upload_sessions[session_id].get('cancelled'):
                return jsonify({'error': 'Upload cancelled'}), 499
            upload_sessions[session_id] = {'cancelled': False, 'temp_files': []}
        
        files = request.files.getlist('file')
        if not files:
            return jsonify({'error': 'No files provided'}), 400
        
        uploaded_files = []
        temp_files = []
        
        try:
            for file in files:
                # Check cancellation status
                with upload_lock:
                    if upload_sessions[session_id]['cancelled']:
                        # Clean up any temp files created so far
                        for temp_file in temp_files:
                            try:
                                if os.path.exists(temp_file):
                                    os.remove(temp_file)
                            except:
                                pass
                        return jsonify({'error': 'Upload cancelled'}), 499
                
                if file.filename == '':
                    continue
                    
                relative_path = request.form.get(f'path_{files.index(file)}', '')
                if not relative_path:
                    relative_path = file.filename
                
                filename = secure_filename(os.path.basename(relative_path))
                
                dir_path = os.path.dirname(relative_path)
                if dir_path:
                    full_dir = os.path.join(base_dir, secure_filename(dir_path))
                    os.makedirs(full_dir, exist_ok=True)
                    save_path = os.path.join(full_dir, filename)
                else:
                    save_path = os.path.join(base_dir, filename)
                
                counter = 1
                name, ext = os.path.splitext(filename)
                while os.path.exists(save_path):
                    filename = f"{name}_{counter}{ext}"
                    if dir_path:
                        full_dir = os.path.join(base_dir, secure_filename(dir_path))
                        save_path = os.path.join(full_dir, filename)
                    else:
                        save_path = os.path.join(base_dir, filename)
                    counter += 1
                
                # Save file in chunks to allow cancellation
                chunk_size = 8192
                with open(save_path, 'wb') as f:
                    while True:
                        # Check cancellation before each chunk
                        with upload_lock:
                            if upload_sessions[session_id]['cancelled']:
                                f.close()
                                # Clean up partial file
                                try:
                                    os.remove(save_path)
                                except:
                                    pass
                                # Clean up other temp files
                                for temp_file in temp_files:
                                    try:
                                        if os.path.exists(temp_file):
                                            os.remove(temp_file)
                                    except:
                                        pass
                                return jsonify({'error': 'Upload cancelled'}), 499
                        
                        chunk = file.stream.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                
                temp_files.append(save_path)
                uploaded_files.append(filename)
            
            # Upload completed successfully
            with upload_lock:
                if session_id in upload_sessions:
                    del upload_sessions[session_id]
            
            return jsonify({'success': True, 'files': uploaded_files})
            
        except Exception as e:
            # Clean up on error
            for temp_file in temp_files:
                try:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                except:
                    pass
            
            with upload_lock:
                if session_id in upload_sessions:
                    del upload_sessions[session_id]
            
            return jsonify({'error': str(e)}), 500

    @app.route('/cancel-upload', methods=['POST'])
    def cancel_upload():
        if not check_auth():
            return jsonify({'error': 'Unauthorized'}), 403
        
        data = request.get_json()
        session_id = data.get('session_id')
        
        if not session_id:
            return jsonify({'error': 'No session ID provided'}), 400
        
        with upload_lock:
            if session_id in upload_sessions:
                upload_sessions[session_id]['cancelled'] = True
                return jsonify({'success': True})
        
        return jsonify({'error': 'Session not found'}), 404

    @app.route('/delete/<path:filename>', methods=['POST'])
    def delete_file(filename):
        if not check_auth():
            return redirect(url_for('login'))
            
        if not allow_delete:
            abort(403)
            
        full_path = os.path.join(base_dir, filename)
        if os.path.exists(full_path):
            if os.path.isdir(full_path):
                shutil.rmtree(full_path)
            else:
                os.remove(full_path)
        return redirect(url_for('index'))

    return app


LOGIN_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>LocalShare - Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { 
            font-family: Arial, sans-serif; 
            margin: 0; 
            padding: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .login-container {
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
            width: 90%;
            max-width: 400px;
        }
        h1 { 
            margin: 0 0 30px 0; 
            text-align: center; 
            color: #333;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #555;
            font-weight: bold;
        }
        input[type="password"] {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 5px;
            font-size: 16px;
            box-sizing: border-box;
            transition: border-color 0.3s;
        }
        input[type="password"]:focus {
            outline: none;
            border-color: #667eea;
        }
        .btn {
            width: 100%;
            background: #667eea;
            color: white;
            padding: 12px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .btn:hover {
            background: #5568d3;
        }
        .error {
            background: #fee;
            color: #c33;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <h1>🔒 LocalShare</h1>
        {% if error %}
        <div class="error">Invalid PIN. Please try again.</div>
        {% endif %}
        <form method="post">
            <div class="form-group">
                <label for="pin">Enter PIN</label>
                <input type="password" id="pin" name="pin" required autofocus>
            </div>
            <button class="btn" type="submit">Login</button>
        </form>
    </div>
</body>
</html>
"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>LocalShare</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            background: #f5f7fa; 
            padding: 15px;
            color: #333;
        }
        .container { 
            max-width: 1000px; 
            margin: 0 auto; 
            background: white; 
            padding: 20px; 
            border-radius: 12px;
            box-shadow: 0 2px 15px rgba(0,0,0,0.08);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 25px;
            flex-wrap: wrap;
            gap: 10px;
        }
        h1 { color: #2c3e50; font-size: 24px; }
        
        .user-info-group {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .user-count {
            display: inline-flex;
            align-items: center;
            background: #3498db;
            color: white;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            white-space: nowrap;
        }
        .logout-btn {
            background: #e74c3c;
            color: white;
            padding: 6px 12px;
            text-decoration: none;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
        }
        
        /* Upload Area Styles */
        .upload-area { 
            border: 2px dashed #3498db; 
            padding: 25px; 
            text-align: center; 
            margin-bottom: 25px;
            border-radius: 12px;
            background: #f8f9fa;
            transition: all 0.2s ease;
        }
        .upload-area.dragover {
            background: #e3f2fd;
            border-color: #2196f3;
            transform: scale(1.01);
        }
        .upload-area h3 { margin-bottom: 15px; color: #2c3e50; font-size: 18px; }
        
        .input-group {
            display: flex;
            justify-content: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        
        .file-input-wrapper {
            position: relative;
            overflow: hidden;
            display: inline-block;
        }
        .file-input-wrapper input[type="file"] {
            position: absolute;
            left: 0;
            top: 0;
            opacity: 0;
            width: 100%;
            height: 100%;
            cursor: pointer;
        }
        
        .btn { 
            background: #3498db; 
            color: white; 
            padding: 10px 20px; 
            text-decoration: none; 
            border: none; 
            border-radius: 6px; 
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .btn:hover { background: #2980b9; }
        .btn:disabled { 
            background: #95a5a6; 
            cursor: not-allowed;
        }
        .btn-success { background: #27ae60; }
        .btn-success:hover { background: #229954; }
        .btn-danger { background: #e74c3c; }
        .btn-danger:hover { background: #c0392b; }
        .btn-info { background: #9b59b6; }
        .btn-info:hover { background: #8e44ad; }
        
        .progress-container {
            display: none;
            margin-top: 20px;
        }
        .progress-bar {
            width: 100%;
            height: 20px;
            background: #ecf0f1;
            border-radius: 10px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #3498db, #2ecc71);
            width: 0%;
            transition: width 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 11px;
            font-weight: bold;
        }
        .upload-controls {
            margin-top: 10px;
            display: flex;
            gap: 10px;
            justify-content: center;
        }
        
        /* File List Styles */
        h3.section-title { margin-bottom: 15px; font-size: 18px; color: #2c3e50; }
        
        .file-table { 
            width: 100%; 
            border-collapse: collapse;
        }
        .file-table th, .file-table td { 
            padding: 12px; 
            text-align: left; 
            border-bottom: 1px solid #ecf0f1; 
        }
        .file-table th { 
            background: #f1f3f5; 
            color: #555;
            font-weight: 600;
            font-size: 14px;
            border-radius: 6px 6px 0 0;
        }
        .file-table tr:last-child td { border-bottom: none; }
        
        .file-name-cell {
            display: flex;
            align-items: center;
            word-break: break-word;
        }
        .file-icon { margin-right: 10px; font-size: 18px; }
        
        .actions { 
            display: flex; 
            gap: 6px;
            flex-wrap: wrap;
        }
        .btn-small { 
            padding: 6px 12px; 
            font-size: 12px; 
        }

        .muted { 
            color: #95a5a6; 
            text-align: center;
            padding: 40px;
            background: #fafafa;
            border-radius: 8px;
        }
        
        footer { 
            margin-top: 30px; 
            text-align: center; 
            color: #95a5a6; 
            font-size: 13px; 
        }

        /* Mobile Optimizations (Card View) */
        @media (max-width: 768px) {
            .container { padding: 15px; }
            
            .file-table, .file-table thead, .file-table tbody, .file-table th, .file-table td, .file-table tr { 
                display: block; 
            }
            
            .file-table thead tr { 
                position: absolute;
                top: -9999px;
                left: -9999px;
            }
            
            .file-table tr { 
                margin-bottom: 15px; 
                background: #fff;
                border: 1px solid #e1e4e8;
                border-radius: 8px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.03);
            }
            
            .file-table td { 
                border: none;
                border-bottom: 1px solid #eee; 
                position: relative;
                padding: 10px 15px;
                padding-left: 40%; 
                min-height: 40px;
                display: flex;
                align-items: center;
            }
            
            .file-table td:before { 
                position: absolute;
                top: 10px;
                left: 15px;
                width: 35%; 
                padding-right: 10px; 
                white-space: nowrap;
                font-weight: bold;
                color: #7f8c8d;
                font-size: 13px;
            }
            
            .file-table td:nth-of-type(1):before { content: "Name"; }
            .file-table td:nth-of-type(2):before { content: "Size"; }
            .file-table td:nth-of-type(3):before { content: "Date"; }
            .file-table td:nth-of-type(4):before { content: "Actions"; }
            
            /* Special styling for Name row on mobile to give it full width */
            .file-table td:nth-of-type(1) {
                padding-left: 15px;
                background: #fafafa;
                border-radius: 8px 8px 0 0;
                font-weight: 500;
            }
            .file-table td:nth-of-type(1):before { display: none; }
            
            /* Special styling for Actions row */
            .file-table td:nth-of-type(4) {
                padding-left: 15px;
                padding-bottom: 15px;
                display: block;
            }
            .file-table td:nth-of-type(4):before { 
                display: none; /* Hide "Actions" label */
            }
            
            .actions {
                width: 100%;
                justify-content: stretch;
            }
            .actions .btn {
                flex: 1;
                text-align: center;
            }
            
            .input-group {
                flex-direction: column;
            }
            .file-input-wrapper {
                width: 100%;
            }
            .file-input-wrapper .btn {
                width: 100%;
            }
        }
    </style>
</head>
<body>
  <div class="container">
    <div class="header">
        <h1>🔗 LocalShare</h1>
        <div class="user-info-group">
            <div class="user-count">
                <span style="margin-right:5px">👥</span> <span id="connectedCount">0</span>
            </div>
            {% if pin_required %}
            <a href="{{ url_for('logout') }}" class="logout-btn">Logout</a>
            {% endif %}
        </div>
    </div>
    
    <div class="upload-area" id="uploadArea">
      <h3>📤 Upload Files</h3>
      <div class="input-group">
          <div class="file-input-wrapper">
            <label for="fileInput" class="btn">Select Files</label>
            <input type="file" id="fileInput" multiple>
          </div>
          <div class="file-input-wrapper">
            <label for="folderInput" class="btn btn-success">Select Folder</label>
            <input type="file" id="folderInput" webkitdirectory directory multiple>
          </div>
      </div>
      <p style="margin-top: 15px; color: #95a5a6; font-size: 13px;">or drag and drop here</p>
      
      <div class="progress-container" id="progressContainer">
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill">0%</div>
        </div>
        <div style="margin-top: 5px; font-size: 13px; color: #7f8c8d;" id="uploadStatus">Preparing...</div>
        <div class="upload-controls">
          <button class="btn btn-danger btn-small" id="cancelBtn" onclick="cancelUpload()">✕ Cancel Upload</button>
        </div>
      </div>
    </div>

    <h3 class="section-title">📋 Files & Folders</h3>
    {% if files %}
      <table class="file-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Size</th>
            <th>Modified</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {% for f in files %}
          <tr>
            <td>
              <div class="file-name-cell">
                  <span class="file-icon">{% if f.is_dir %}📁{% else %}📄{% endif %}</span>
                  {{ f.name }}
              </div>
            </td>
            <td>{{ f.size_h }}</td>
            <td>{{ f.mtime_h }}</td>
            <td>
              <div class="actions">
                {% if f.can_stream %}
                <a class="btn btn-small btn-info" href="{{ url_for('stream', filename=f.name) }}">
                  {% if f.is_image %}View{% elif f.is_video %}Stream{% else %}Play{% endif %}
                </a>
                {% endif %}
                <a class="btn btn-small" href="{{ url_for('files', filename=f.name) }}">
                  {% if f.is_dir %}ZIP{% else %}Down{% endif %}
                </a>
                {% if allow_delete %}
                <form action="{{ url_for('delete_file', filename=f.name) }}" method="post" style="display:inline; flex: 1;">
                  <button class="btn btn-small btn-danger" style="width:100%" type="submit" 
                          onclick="return confirm('Delete {{ f.name }}?')">Del</button>
                </form>
                {% endif %}
              </div>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="muted">🔭 No files yet. Upload something to get started!</p>
    {% endif %}
  </div>

  <footer>
    🌐 Shared via Local Network
  </footer>

  <script>
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    const folderInput = document.getElementById('folderInput');
    const progressContainer = document.getElementById('progressContainer');
    const progressFill = document.getElementById('progressFill');
    const uploadStatus = document.getElementById('uploadStatus');
    const cancelBtn = document.getElementById('cancelBtn');

    let currentUploadXHR = null;
    let currentSessionId = null;

    function updateUserCount() {
      fetch('{{ url_for("stats") }}')
        .then(response => response.json())
        .then(data => {
          document.getElementById('connectedCount').textContent = data.connected_users;
        })
        .catch(err => console.error('Failed to fetch stats:', err));
    }
    
    updateUserCount();
    setInterval(updateUserCount, 5000);

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      uploadArea.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
      e.preventDefault();
      e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
      uploadArea.addEventListener(eventName, () => {
        uploadArea.classList.add('dragover');
      }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
      uploadArea.addEventListener(eventName, () => {
        uploadArea.classList.remove('dragover');
      }, false);
    });

    uploadArea.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
      const dt = e.dataTransfer;
      const items = dt.items;
      
      if (items) {
        const files = [];
        const promises = [];
        
        for (let i = 0; i < items.length; i++) {
          const item = items[i].webkitGetAsEntry();
          if (item) {
            promises.push(traverseFileTree(item, '', files));
          }
        }
        
        Promise.all(promises).then(() => {
          if (files.length > 0) {
            uploadFiles(files);
          }
        });
      }
    }

    function traverseFileTree(item, path, files) {
      return new Promise((resolve) => {
        if (item.isFile) {
          item.file(file => {
            file.relativePath = path + file.name;
            files.push(file);
            resolve();
          });
        } else if (item.isDirectory) {
          const dirReader = item.createReader();
          dirReader.readEntries(entries => {
            const promises = [];
            for (let i = 0; i < entries.length; i++) {
              promises.push(traverseFileTree(entries[i], path + item.name + '/', files));
            }
            Promise.all(promises).then(resolve);
          });
        }
      });
    }

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        uploadFiles(Array.from(e.target.files));
      }
    });

    folderInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        uploadFiles(Array.from(e.target.files));
      }
    });

    function cancelUpload() {
      if (currentSessionId && currentUploadXHR) {
        // Send cancel request to server
        fetch('{{ url_for("cancel_upload") }}', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ session_id: currentSessionId })
        });

        // Abort the XHR request
        currentUploadXHR.abort();
        
        uploadStatus.textContent = 'Upload cancelled';
        uploadStatus.style.color = '#e74c3c';
        progressFill.style.background = '#e74c3c';
        
        setTimeout(() => {
          progressContainer.style.display = 'none';
          progressFill.style.background = 'linear-gradient(90deg, #3498db, #2ecc71)';
          uploadStatus.style.color = '#7f8c8d';
          currentUploadXHR = null;
          currentSessionId = null;
        }, 2000);
      }
    }

    function uploadFiles(files) {
      const formData = new FormData();
      
      // Generate unique session ID
      currentSessionId = 'upload_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
      
      files.forEach((file, index) => {
        formData.append('file', file);
        const relativePath = file.webkitRelativePath || file.relativePath || file.name;
        formData.append(`path_${index}`, relativePath);
      });

      progressContainer.style.display = 'block';
      progressFill.style.width = '0%';
      progressFill.textContent = '0%';
      uploadStatus.textContent = `Uploading ${files.length} file(s)...`;
      uploadStatus.style.color = '#7f8c8d';
      cancelBtn.disabled = false;

      currentUploadXHR = new XMLHttpRequest();

      currentUploadXHR.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percentComplete = Math.round((e.loaded / e.total) * 100);
          progressFill.style.width = percentComplete + '%';
          progressFill.textContent = percentComplete + '%';
        }
      });

      currentUploadXHR.addEventListener('load', () => {
        if (currentUploadXHR.status === 200) {
          progressFill.style.width = '100%';
          progressFill.textContent = '✓';
          uploadStatus.textContent = 'Complete! Refreshing...';
          cancelBtn.disabled = true;
          setTimeout(() => {
            location.reload();
          }, 1000);
        } else if (currentUploadXHR.status === 499) {
          // Upload was cancelled
          uploadStatus.textContent = 'Upload cancelled';
          uploadStatus.style.color = '#e74c3c';
          progressFill.style.background = '#e74c3c';
        } else {
          uploadStatus.textContent = 'Upload failed.';
          uploadStatus.style.color = '#e74c3c';
          cancelBtn.disabled = true;
        }
      });

      currentUploadXHR.addEventListener('error', () => {
        uploadStatus.textContent = 'Upload failed.';
        uploadStatus.style.color = '#e74c3c';
        cancelBtn.disabled = true;
      });

      currentUploadXHR.addEventListener('abort', () => {
        uploadStatus.textContent = 'Upload cancelled';
        uploadStatus.style.color = '#e74c3c';
        progressFill.style.background = '#e74c3c';
        cancelBtn.disabled = true;
      });

      currentUploadXHR.open('POST', '{{ url_for("upload_file") }}');
      currentUploadXHR.setRequestHeader('X-Upload-Session-ID', currentSessionId);
      currentUploadXHR.send(formData);

      fileInput.value = '';
      folderInput.value = '';
    }
  </script>
</body>
</html>
"""


IMAGE_VIEWER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>View Image - {{ filename }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial, sans-serif;
            background: #111;
            display: flex;
            flex-direction: column;
            height: 100vh;
            color: white;
        }
        .header {
            background: rgba(30,30,30,0.95);
            padding: 15px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #333;
        }
        .filename { font-size: 16px; font-weight: bold; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 60%; }
        .controls a {
            background: #3498db;
            color: white;
            padding: 8px 15px;
            text-decoration: none;
            border-radius: 4px;
            font-size: 13px;
            margin-left: 5px;
        }
        .image-container {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
            padding: 10px;
        }
        img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="filename">{{ filename }}</div>
        <div class="controls">
            <a href="{{ file_url }}" download>Download</a>
            <a href="javascript:history.back()">Back</a>
        </div>
    </div>
    <div class="image-container">
        <img src="{{ file_url }}" alt="{{ filename }}">
    </div>
</body>
</html>
"""


VIDEO_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Stream - {{ filename }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', sans-serif;
            background: #0f0f0f;
            color: #eee;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .header {
            width: 100%;
            padding: 15px 20px;
            background: #1f1f1f;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 2px 10px rgba(0,0,0,0.3);
            position: sticky;
            top: 0;
            z-index: 100;
        }
        .title-group { overflow: hidden; }
        .filename { 
            font-size: 16px; 
            font-weight: 600; 
            white-space: nowrap; 
            overflow: hidden; 
            text-overflow: ellipsis; 
        }
        .back-btn {
            color: #aaa;
            text-decoration: none;
            font-size: 14px;
            margin-right: 15px;
        }
        .download-btn {
            background: #3498db;
            color: white;
            padding: 6px 12px;
            text-decoration: none;
            border-radius: 4px;
            font-size: 13px;
            white-space: nowrap;
        }
        
        .main-content {
            width: 100%;
            max-width: 1000px;
            padding: 20px;
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        video {
            width: 100%;
            max-height: 70vh;
            background: black;
            border-radius: 8px;
            box-shadow: 0 5px 25px rgba(0,0,0,0.5);
            outline: none;
        }
        
        .controls-area {
            background: #1f1f1f;
            padding: 20px;
            border-radius: 8px;
            margin-top: 20px;
        }
        
        h4 { margin-bottom: 15px; color: #aaa; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; }
        
        .subtitle-controls {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        
        .control-group {
            flex: 1;
            min-width: 250px;
        }
        
        label { display: block; margin-bottom: 8px; font-size: 13px; color: #888; }
        
        select, .file-input-trigger {
            width: 100%;
            padding: 10px;
            background: #2a2a2a;
            border: 1px solid #444;
            color: white;
            border-radius: 4px;
            font-size: 14px;
        }
        
        .file-input-wrapper { position: relative; overflow: hidden; }
        .file-input-wrapper input[type=file] {
            position: absolute; left: 0; top: 0; opacity: 0; width: 100%; height: 100%; cursor: pointer;
        }
        .file-input-trigger {
            display: block;
            text-align: center;
            cursor: pointer;
            background: #34495e;
        }
        .file-input-trigger:hover { background: #2c3e50; }
        
        .status-msg {
            margin-top: 10px;
            font-size: 13px;
            color: #2ecc71;
            display: none;
        }
        
    </style>
</head>
<body>
    <div class="header">
        <div class="title-group">
            <a href="{{ url_for('index') }}" class="back-btn">← Back</a>
            <span class="filename">{{ filename }}</span>
        </div>
        <a href="{{ file_url }}" class="download-btn" download>Download</a>
    </div>

    <div class="main-content">
        <video id="videoPlayer" controls preload="metadata" crossorigin="anonymous">
            <source src="{{ file_url }}" type="video/mp4">
            <track id="subtitleTrack" label="Subtitle" kind="subtitles" srclang="en" default>
            Your browser does not support the video tag.
        </video>

        <div class="controls-area">
            <h4>💬 Subtitles</h4>
            <div class="subtitle-controls">
                
                <div class="control-group">
                    <label>Select from Uploaded Files:</label>
                    <select id="serverSubSelect">
                        <option value="">-- Select a subtitle --</option>
                        {% for sub in subtitles %}
                        <option value="{{ sub }}">{{ sub }}</option>
                        {% endfor %}
                    </select>
                </div>

                <div class="control-group">
                    <label>Load from Your Device:</label>
                    <div class="file-input-wrapper">
                        <div class="file-input-trigger">📂 Choose .srt or .vtt file</div>
                        <input type="file" id="localSubInput" accept=".srt,.vtt">
                    </div>
                </div>
            </div>
            <div id="subStatus" class="status-msg">Subtitle loaded successfully!</div>
        </div>
    </div>

    <script>
        const video = document.getElementById('videoPlayer');
        const track = document.getElementById('subtitleTrack');
        const serverSelect = document.getElementById('serverSubSelect');
        const localInput = document.getElementById('localSubInput');
        const statusMsg = document.getElementById('subStatus');

        // Prevent seeking issues by ensuring video can seek properly
        video.addEventListener('loadedmetadata', function() {
            console.log('Video metadata loaded. Duration:', video.duration);
        });

        // Handle seeking - prevent reset to beginning
        let isSeeking = false;
        let targetTime = 0;

        video.addEventListener('seeking', function() {
            isSeeking = true;
            targetTime = video.currentTime;
            console.log('Seeking to:', targetTime);
        });

        video.addEventListener('seeked', function() {
            isSeeking = false;
            console.log('Seeked to:', video.currentTime);
        });

        // Prevent autoplay from interfering with seeking
        video.addEventListener('timeupdate', function() {
            if (isSeeking && Math.abs(video.currentTime - targetTime) > 1) {
                console.log('Correcting seek position');
                video.currentTime = targetTime;
            }
        });

        // Helper to convert SRT content to WebVTT blob URL
        function srtToVttBlob(srtContent) {
            let vtt = "WEBVTT\n\n";
            // Replace comma with dot in timestamps
            vtt += srtContent.replace(/(\\d{2}:\\d{2}:\\d{2}),(\\d{3})/g, '$1.$2');
            const blob = new Blob([vtt], { type: 'text/vtt' });
            return URL.createObjectURL(blob);
        }

        function loadSubtitle(url, isLocalFile = false) {
            // If it's a local file object or a direct server URL
            if (isLocalFile) {
                // It's a File object
                const reader = new FileReader();
                reader.onload = function(e) {
                    let content = e.target.result;
                    let finalUrl;
                    
                    // Simple check if it looks like SRT (has arrows with comma)
                    if (content.indexOf('-->') > -1 && content.match(/,(\\d{3})/)) {
                        finalUrl = srtToVttBlob(content);
                    } else {
                        // Assume VTT or compatible
                        const blob = new Blob([content], { type: 'text/vtt' });
                        finalUrl = URL.createObjectURL(blob);
                    }
                    
                    track.src = finalUrl;
                    video.textTracks[0].mode = 'showing';
                    showStatus('Local subtitle loaded');
                };
                reader.readAsText(url);
            } else {
                // It's a URL from server
                fetch(url)
                    .then(r => r.text())
                    .then(content => {
                        let finalUrl;
                        // Check if needs conversion (if server sent .srt)
                        if (url.endsWith('.srt')) {
                            finalUrl = srtToVttBlob(content);
                        } else {
                            const blob = new Blob([content], { type: 'text/vtt' });
                            finalUrl = URL.createObjectURL(blob);
                        }
                        track.src = finalUrl;
                        video.textTracks[0].mode = 'showing';
                        showStatus('Server subtitle loaded');
                    })
                    .catch(err => console.error('Error loading sub:', err));
            }
        }

        function showStatus(msg) {
            statusMsg.textContent = msg;
            statusMsg.style.display = 'block';
            setTimeout(() => statusMsg.style.display = 'none', 3000);
        }

        // Handle Server Select
        serverSelect.addEventListener('change', (e) => {
            if(e.target.value) {
                // We use the view route to fetch the raw text content
                loadSubtitle('/view/' + encodeURIComponent(e.target.value), false);
                localInput.value = ''; // Reset other input
            }
        });

        // Handle Local Input
        localInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                loadSubtitle(file, true);
                serverSelect.value = ''; // Reset other input
            }
        });
    </script>
</body>
</html>
"""


AUDIO_PLAYER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>Play Audio - {{ filename }}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            color: white;
        }
        .container {
            background: rgba(255,255,255,0.1);
            backdrop-filter: blur(10px);
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.3);
            text-align: center;
            max-width: 600px;
            width: 90%;
        }
        .icon {
            font-size: 80px;
            margin-bottom: 20px;
        }
        .filename {
            font-size: 20px;
            font-weight: bold;
            margin-bottom: 30px;
            word-break: break-word;
        }
        audio {
            width: 100%;
            margin-bottom: 20px;
        }
        .btn {
            background: white;
            color: #667eea;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 25px;
            font-size: 16px;
            margin: 10px;
            display: inline-block;
            font-weight: bold;
        }
        .btn:hover {
            background: #f0f0f0;
            transform: translateY(-2px);
            transition: all 0.3s;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="icon">🎵</div>
        <div class="filename">{{ filename }}</div>
        <audio id="audioPlayer" controls preload="metadata">
            <source src="{{ file_url }}" type="audio/mpeg">
            Your browser does not support the audio tag.
        </audio>
        <div>
            <a class="btn" href="{{ file_url }}" download>📥 Download</a>
            <a class="btn" href="javascript:history.back()">Back</a>
        </div>
    </div>

    <script>
        const audio = document.getElementById('audioPlayer');

        // Prevent seeking issues by ensuring audio can seek properly
        audio.addEventListener('loadedmetadata', function() {
            console.log('Audio metadata loaded. Duration:', audio.duration);
        });

        // Handle seeking - prevent reset to beginning
        let isSeeking = false;
        let targetTime = 0;

        audio.addEventListener('seeking', function() {
            isSeeking = true;
            targetTime = audio.currentTime;
            console.log('Seeking to:', targetTime);
        });

        audio.addEventListener('seeked', function() {
            isSeeking = false;
            console.log('Seeked to:', audio.currentTime);
        });

        // Prevent any interference with seeking
        audio.addEventListener('timeupdate', function() {
            if (isSeeking && Math.abs(audio.currentTime - targetTime) > 1) {
                console.log('Correcting seek position');
                audio.currentTime = targetTime;
            }
        });
    </script>
</body>
</html>
"""


def get_default_dir():
    home = os.path.expanduser("~")
    if "ANDROID_STORAGE" in os.environ or "TERMUX_VERSION" in os.environ:
        return os.path.join(home, "storage", "shared", "LocalShare")
    elif sys.platform.startswith("win"):
        return os.path.join(home, "LocalShare")
    elif sys.platform == "darwin":
        return os.path.join(home, "LocalShare")
    else:
        return os.path.join(home, "LocalShare")


def main():
    global shared_dir
    
    parser = argparse.ArgumentParser(description="Simple LAN file sharing hub (upload/download)")
    parser.add_argument("--dir", default=os.environ.get("LAN_SHARE_DIR", get_default_dir()),
                        help="Directory to store shared files")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAN_SHARE_PORT", "5000")), help="Port to bind (default: 5000)")
    parser.add_argument("--disable-delete", action="store_true", help="Disable delete button")
    parser.add_argument("--pin", default=os.environ.get("LAN_SHARE_PIN", ""), help="Optional PIN required for access")
    parser.add_argument("--update", action="store_true", help="Update to latest version")
    args = parser.parse_args()

    if args.update:
        update_script()
        return

    register_signal_handlers()

    shared_dir = args.dir
    allow_delete = not args.disable_delete  # <-- magic line

    os.makedirs(args.dir, exist_ok=True)
    app_obj = build_app(args.dir, allow_delete=allow_delete, pin=args.pin if args.pin else None)

    print(f"Serving directory: {args.dir}")
    print(f"Open from other devices: http://<your_local_ip>:{args.port}")
    if args.pin:
        print(f"PIN protection enabled. Users must login with PIN: {args.pin}")
    print(f"Delete enabled: {allow_delete}")
    print("Press Ctrl+C to stop the server and clean up files.")

    try:
        app_obj.run(host=args.host, port=args.port, threaded=True)
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        cleanup_shared_files()

if __name__ == "__main__":
    main()
