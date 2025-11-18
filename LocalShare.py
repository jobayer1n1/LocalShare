import os
import argparse
import signal
import sys
import shutil
from flask import Flask, request, send_file, render_template_string, redirect, url_for, abort, session, jsonify
import time
from datetime import datetime
import subprocess
from werkzeug.utils import secure_filename
import zipfile
import io

app = None
shared_dir = ""

# ---------- UPDATE FEATURE ----------
SCRIPT_URL = "https://raw.githubusercontent.com/jobayer1n1/LocalShare/main/LocalShare.py"   # ← replace with your raw GitHub path

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
        self.is_dir = os.path.isdir(path)
        stat = os.stat(path)
        self.size = stat.st_size if not self.is_dir else self._get_dir_size(path)
        self.mtime = stat.st_mtime
        
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
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB max upload
    
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
            
        # If it's a directory, create a zip file on the fly
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

    @app.route('/upload', methods=['POST'])
    def upload_file():
        if not check_auth():
            return jsonify({'error': 'Unauthorized'}), 403
            
        files = request.files.getlist('file')
        if not files:
            return jsonify({'error': 'No files provided'}), 400
        
        uploaded_files = []
        for file in files:
            if file.filename == '':
                continue
                
            # Get the relative path from webkitRelativePath if available
            relative_path = request.form.get(f'path_{files.index(file)}', '')
            if not relative_path:
                relative_path = file.filename
            
            # Secure the filename
            filename = secure_filename(os.path.basename(relative_path))
            
            # Create directory structure if needed
            dir_path = os.path.dirname(relative_path)
            if dir_path:
                full_dir = os.path.join(base_dir, secure_filename(dir_path))
                os.makedirs(full_dir, exist_ok=True)
                save_path = os.path.join(full_dir, filename)
            else:
                save_path = os.path.join(base_dir, filename)
            
            # Handle duplicate filenames
            counter = 1
            original_save_path = save_path
            name, ext = os.path.splitext(filename)
            while os.path.exists(save_path):
                filename = f"{name}_{counter}{ext}"
                if dir_path:
                    full_dir = os.path.join(base_dir, secure_filename(dir_path))
                    save_path = os.path.join(full_dir, filename)
                else:
                    save_path = os.path.join(base_dir, filename)
                counter += 1
            
            file.save(save_path)
            uploaded_files.append(filename)
        
        return jsonify({'success': True, 'files': uploaded_files})

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
            width: 100%;
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
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            background: #f5f7fa; 
            padding: 20px;
        }
        .container { 
            max-width: 1000px; 
            margin: 0 auto; 
            background: white; 
            padding: 30px; 
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 30px;
        }
        h1 { color: #2c3e50; }
        .logout-btn {
            background: #e74c3c;
            color: white;
            padding: 8px 16px;
            text-decoration: none;
            border-radius: 5px;
            font-size: 14px;
        }
        .logout-btn:hover { background: #c0392b; }
        .upload-area { 
            border: 3px dashed #3498db; 
            padding: 40px; 
            text-align: center; 
            margin-bottom: 30px;
            border-radius: 10px;
            background: #f8f9fa;
            transition: all 0.3s;
        }
        .upload-area.dragover {
            background: #e3f2fd;
            border-color: #2196f3;
        }
        .upload-area h3 { margin-bottom: 20px; color: #2c3e50; }
        .file-input-wrapper {
            position: relative;
            display: inline-block;
            margin: 10px;
        }
        .file-input-wrapper input[type="file"] {
            position: absolute;
            opacity: 0;
            width: 0;
            height: 0;
        }
        .btn { 
            background: #3498db; 
            color: white; 
            padding: 12px 24px; 
            text-decoration: none; 
            border: none; 
            border-radius: 5px; 
            cursor: pointer;
            font-size: 14px;
            transition: background 0.3s;
            display: inline-block;
        }
        .btn:hover { background: #2980b9; }
        .btn-success { background: #27ae60; }
        .btn-success:hover { background: #229954; }
        .btn-danger { background: #e74c3c; }
        .btn-danger:hover { background: #c0392b; }
        .btn-small { padding: 6px 12px; font-size: 13px; }
        
        .progress-container {
            display: none;
            margin: 20px 0;
        }
        .progress-bar {
            width: 100%;
            height: 30px;
            background: #ecf0f1;
            border-radius: 15px;
            overflow: hidden;
            position: relative;
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
            font-weight: bold;
        }
        .upload-status {
            margin-top: 10px;
            text-align: center;
            color: #7f8c8d;
        }
        
        table { 
            width: 100%; 
            border-collapse: collapse;
            margin-top: 20px;
        }
        th, td { 
            padding: 12px; 
            text-align: left; 
            border-bottom: 1px solid #ecf0f1; 
        }
        th { 
            background: #34495e; 
            color: white;
            font-weight: 600;
        }
        tr:hover { background: #f8f9fa; }
        .actions { 
            display: flex; 
            gap: 8px;
            align-items: center;
        }
        .file-icon {
            margin-right: 8px;
        }
        .muted { 
            color: #95a5a6; 
            text-align: center;
            padding: 40px;
        }
        footer { 
            margin-top: 30px; 
            text-align: center; 
            color: #7f8c8d; 
            font-size: 14px; 
        }
    </style>
</head>
<body>
  <div class="container">
    <div class="header">
        <h1>🖥️ LocalShare 🖥️</h1>
        {% if pin_required %}
        <a href="{{ url_for('logout') }}" class="logout-btn">Logout</a>
        {% endif %}
    </div>
    
    <div class="upload-area" id="uploadArea">
      <h3>📤 Upload Files or Folders</h3>
      <div class="file-input-wrapper">
        <label for="fileInput" class="btn">Choose Files</label>
        <input type="file" id="fileInput" multiple>
      </div>
      <div class="file-input-wrapper">
        <label for="folderInput" class="btn btn-success">Choose Folder</label>
        <input type="file" id="folderInput" webkitdirectory directory multiple>
      </div>
      <p style="margin-top: 15px; color: #7f8c8d;">or drag and drop files/folders here</p>
      
      <div class="progress-container" id="progressContainer">
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill">0%</div>
        </div>
        <div class="upload-status" id="uploadStatus">Preparing upload...</div>
      </div>
    </div>

    <h3>📋 Files & Folders</h3>
    {% if files %}
      <table>
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
              <span class="file-icon">{% if f.is_dir %}📁{% else %}📄{% endif %}</span>
              {{ f.name }}
            </td>
            <td>{{ f.size_h }}</td>
            <td>{{ f.mtime_h }}</td>
            <td>
              <div class="actions">
                <a class="btn btn-small" href="{{ url_for('files', filename=f.name) }}">
                  {% if f.is_dir %}Download ZIP{% else %}Download{% endif %}
                </a>
                {% if allow_delete %}
                <form action="{{ url_for('delete_file', filename=f.name) }}" method="post" style="display:inline">
                  <button class="btn btn-small btn-danger" type="submit" 
                          onclick="return confirm('Delete {{ f.name }}?')">Delete</button>
                </form>
                {% endif %}
              </div>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="muted">📭 No files yet. Upload something to get started!</p>
    {% endif %}
  </div>

  <footer>
    🌐 Running on your LAN. Share this URL with devices on the same network.
  </footer>

  <script>
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    const folderInput = document.getElementById('folderInput');
    const progressContainer = document.getElementById('progressContainer');
    const progressFill = document.getElementById('progressFill');
    const uploadStatus = document.getElementById('uploadStatus');

    // Drag and drop handlers
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

    function uploadFiles(files) {
      const formData = new FormData();
      
      files.forEach((file, index) => {
        formData.append('file', file);
        const relativePath = file.webkitRelativePath || file.relativePath || file.name;
        formData.append(`path_${index}`, relativePath);
      });

      progressContainer.style.display = 'block';
      progressFill.style.width = '0%';
      progressFill.textContent = '0%';
      uploadStatus.textContent = `Uploading ${files.length} file(s)...`;

      const xhr = new XMLHttpRequest();

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percentComplete = Math.round((e.loaded / e.total) * 100);
          progressFill.style.width = percentComplete + '%';
          progressFill.textContent = percentComplete + '%';
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status === 200) {
          progressFill.style.width = '100%';
          progressFill.textContent = '✓ Complete';
          uploadStatus.textContent = 'Upload completed successfully!';
          setTimeout(() => {
            location.reload();
          }, 1000);
        } else {
          uploadStatus.textContent = 'Upload failed. Please try again.';
          uploadStatus.style.color = '#e74c3c';
        }
      });

      xhr.addEventListener('error', () => {
        uploadStatus.textContent = 'Upload failed. Please try again.';
        uploadStatus.style.color = '#e74c3c';
      });

      xhr.open('POST', '{{ url_for("upload_file") }}');
      xhr.send(formData);

      // Reset file inputs
      fileInput.value = '';
      folderInput.value = '';
    }
  </script>
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
    parser.add_argument("--pin", default=os.environ.get("LAN_SHARE_PIN", ""), help="Optional PIN required for access")
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
        print(f"PIN protection enabled. Users must login with PIN: {args.pin}")
    print("Press Ctrl+C to stop the server and clean up files.")

    try:
        app_obj.run(host=args.host, port=args.port, threaded=True)
    except Exception as e:
        print(f"Server error: {e}")
    finally:
        cleanup_shared_files()


if __name__ == "__main__":
    main()
