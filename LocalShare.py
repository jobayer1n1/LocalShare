import os
import argparse
import signal
import sys
import shutil
from flask import Flask, request, send_file, render_template_string, redirect, url_for, abort
import time
from datetime import datetime
import subprocess

app = None
shared_dir = ""

# ---------- UPDATE FEATURE ----------
SCRIPT_URL = "https://raw.githubusercontent.com/<username>/<repo>/main/LocalShare.py"   # ← replace with your raw GitHub path

def update_script():
    print("Downloading latest version...")
    try:
        result = subprocess.run(
            ["curl", "-L", SCRIPT_URL, "-o", sys.argv[0]],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            print("Update failed:", result.stderr)
            sys.exit(1)

        print("Updated successfully! Restart normally.")
        sys.exit(0)
    except Exception as e:
        print("Update failed:", e)
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
        pass  # Windows doesn't always allow SIGTERM


class FileInfo:
    def __init__(self, path, base_dir):
        self.path = path
        self.name = os.path.basename(path)
        self.relpath = os.path.relpath(path, base_dir)
        stat = os.stat(path)
        self.size = stat.st_size
        self.mtime = stat.st_mtime
        
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
    app.config['BASE_DIR'] = base_dir
    app.config['ALLOW_DELETE'] = allow_delete
    app.config['PIN'] = pin
    
    @app.route('/')
    def index():
        files = []
        for dirpath, dirnames, filenames in os.walk(base_dir):
            for f in filenames:
                full_path = os.path.join(dirpath, f)
                files.append(FileInfo(full_path, base_dir))
        files.sort(key=lambda x: x.mtime, reverse=True)
        
        return render_template_string(HTML_TEMPLATE, 
                                   files=files, 
                                   allow_delete=allow_delete,
                                   pin_required=pin is not None)

    @app.route('/files/<path:filename>')
    def files(filename):
        full_path = os.path.join(base_dir, filename)
        if not os.path.exists(full_path):
            abort(404)
        return send_file(full_path)

    @app.route('/upload', methods=['POST'])
    def upload_file():
        if app.config['PIN'] and request.form.get('pin') != app.config['PIN']:
            return "Invalid PIN", 403
            
        if 'file' not in request.files:
            return redirect(request.referrer or '/')
            
        file = request.files['file']
        if file.filename == '':
            return redirect(request.referrer or '/')
            
        if file:
            filename = os.path.basename(file.filename)
            save_path = os.path.join(base_dir, filename)
            counter = 1
            name, ext = os.path.splitext(filename)
            while os.path.exists(save_path):
                filename = f"{name}_{counter}{ext}"
                save_path = os.path.join(base_dir, filename)
                counter += 1
            file.save(save_path)
            
        return redirect(request.referrer or '/')

    @app.route('/delete/<path:filename>', methods=['POST'])
    def delete_file(filename):
        if not allow_delete:
            abort(403)
            
        if app.config['PIN'] and request.form.get('pin') != app.config['PIN']:
            return "Invalid PIN", 403
            
        full_path = os.path.join(base_dir, filename)
        if os.path.exists(full_path):
            os.remove(full_path)
        return redirect(request.referrer or '/')

    return app



HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <title>LocalShare</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; }
        .upload-area { border: 2px dashed #ccc; padding: 20px; text-align: center; margin-bottom: 20px; }
        .btn { background: #007cba; color: white; padding: 8px 16px; text-decoration: none; border: none; border-radius: 4px; cursor: pointer; }
        .btn:hover { background: #005a87; }
        .muted { color: #666; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }
        .actions { display: flex; gap: 8px; }
        input[type="password"] { padding: 4px; }
        footer { margin-top: 20px; text-align: center; color: #666; font-size: 0.9em; }
    </style>
</head>
<body>
  <div class="container">
    <h1>LAN Share Hub</h1>
    
    <div class="upload-area">
      <h3 style="margin-top:0">Upload File</h3>
      <form action="{{ url_for('upload_file') }}" method="post" enctype="multipart/form-data">
        <input type="file" name="file" required>
        {% if pin_required %}<input type="password" name="pin" placeholder="PIN" required>{% endif %}
        <button class="btn" type="submit">Upload</button>
      </form>
    </div>

    <h3 style="margin-top:0">Files</h3>
    {% if files %}
      <table>
        <thead>
          <tr><th>Name</th><th>Size</th><th>Modified</th><th></th></tr>
        </thead>
        <tbody>
          {% for f in files %}
          <tr>
            <td><a href="{{ url_for('files', filename=f.name) }}">{{ f.name }}</a></td>
            <td>{{ f.size_h }}</td>
            <td>{{ f.mtime_h }}</td>
            <td>
              <div class="actions">
                <a class="btn" href="{{ url_for('files', filename=f.name) }}">Download</a>
                {% if allow_delete %}
                <form action="{{ url_for('delete_file', filename=f.name) }}" method="post" style="display:inline">
                  {% if pin_required %}<input type="password" name="pin" placeholder="PIN">{% endif %}
                  <button class="btn" type="submit" onclick="return confirm('Delete {{ f.name }}?')">Delete</button>
                </form>
                {% endif %}
              </div>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="muted">No files yet. Upload something!</p>
    {% endif %}
  </div>

  <footer>
    Running on your LAN. Share this URL with devices on the same WiFi.
  </footer>
</body>
</html>
"""


# ----------- AUTO OS DEFAULT DIRECTORY -----------
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
# --------------------------------------------------


def main():
    global shared_dir
    
    parser = argparse.ArgumentParser(description="Simple LAN file sharing hub (upload/download)")
    parser.add_argument("--dir", default=os.environ.get("LAN_SHARE_DIR", get_default_dir()),
                        help="Directory to store shared files")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LAN_SHARE_PORT", "5000")), help="Port to bind (default: 5000)")
    parser.add_argument("--allow-delete", action="store_true", help="Enable delete button")
    parser.add_argument("--pin", default=os.environ.get("LAN_SHARE_PIN", ""), help="Optional PIN required for upload/delete")
    parser.add_argument("--update", action="store_true", help="Update to latest version")
    args = parser.parse_args()

    # UPDATE MODE
    if args.update:
        update_script()
        return

    register_signal_handlers()
    
    shared_dir = args.dir
    app_obj = build_app(args.dir, allow_delete=args.allow_delete, pin=args.pin if args.pin else None)
    os.makedirs(args.dir, exist_ok=True)

    print(f"Serving directory: {args.dir}")
    print(f"Open from other devices: http://<your_local_ip>:{args.port}")
    if args.pin:
        print("Upload PIN is enabled.")
    print("Press Ctrl+C to stop the server and clean up files.")

    try:
        app_obj.run(host=args.host, port=args.port, threaded=True)
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        cleanup_shared_files()


if __name__ == "__main__":
    main()
