import os
import time
import json
import csv
import threading
import requests
from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0 Safari/537.36"
    )
}

def is_file_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "file"

def path_from_file_url(url):
    parsed = urlparse(url)
    path = parsed.path
    if os.name == "nt" and path.startswith("/"):
        # Windows file URLs start with a leading slash
        path = path.lstrip("/")
    return os.path.normpath(path)

class LinkCheckerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Broken Link Checker")
        self.root.geometry("1000x650")
        self.running = False
        self.results = []
        self.build_ui()

    def build_ui(self):
        frm = tk.Frame(self.root)
        frm.pack(fill="x", padx=10, pady=8)

        tk.Label(frm, text="Mode").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="web")
        tk.Radiobutton(frm, text="Web URL", variable=self.mode_var, value="web", command=self.on_mode_change).grid(row=0, column=1, sticky="w")
        tk.Radiobutton(frm, text="Local File or Folder", variable=self.mode_var, value="local", command=self.on_mode_change).grid(row=0, column=2, sticky="w")

        tk.Label(frm, text="Target").grid(row=1, column=0, sticky="w")
        self.target_entry = tk.Entry(frm, width=70)
        self.target_entry.grid(row=1, column=1, columnspan=3, sticky="w", padx=5)

        self.browse_btn = tk.Button(frm, text="Browse", command=self.browse_target)
        self.browse_btn.grid(row=1, column=4, padx=5)

        tk.Label(frm, text="Max Depth").grid(row=2, column=0, sticky="w")
        self.depth_spin = tk.Spinbox(frm, from_=1, to=10, width=5)
        self.depth_spin.grid(row=2, column=1, sticky="w")

        self.same_domain_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frm, text="Stay within same domain for web mode", variable=self.same_domain_var).grid(row=2, column=2, columnspan=2, sticky="w")

        self.start_btn = tk.Button(frm, text="Start Scan", command=self.start_scan)
        self.start_btn.grid(row=3, column=1, pady=8, sticky="w")
        self.stop_btn = tk.Button(frm, text="Stop", command=self.stop_scan, state="disabled")
        self.stop_btn.grid(row=3, column=2, pady=8, sticky="w")

        # Results table
        cols = ("broken", "source", "error", "type")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings")
        self.tree.heading("broken", text="Broken Link or Missing File")
        self.tree.heading("source", text="Found On")
        self.tree.heading("error", text="Error / Status")
        self.tree.heading("type", text="Type")
        self.tree.column("broken", width=420)
        self.tree.column("source", width=300)
        self.tree.column("error", width=120)
        self.tree.column("type", width=80)
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        # Status bar
        self.status = tk.Label(self.root, text="Ready", anchor="w")
        self.status.pack(fill="x")

        # Menu
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Export CSV", command=self.export_csv)
        file_menu.add_command(label="Export JSON", command=self.export_json)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

    def on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "web":
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, "https://")
        else:
            self.target_entry.delete(0, tk.END)

    def browse_target(self):
        mode = self.mode_var.get()
        if mode == "web":
            # no file dialog for web
            return
        # local mode: allow file or folder
        path = filedialog.askopenfilename(title="Select HTML file or Cancel to choose folder")
        if path:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, path)
            return
        folder = filedialog.askdirectory(title="Select folder containing HTML files")
        if folder:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, folder)

    def start_scan(self):
        target = self.target_entry.get().strip()
        if not target:
            messagebox.showerror("Error", "Please enter a target URL or local path.")
            return
        self.running = True
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.tree.delete(*self.tree.get_children())
        self.results = []
        thread = threading.Thread(target=self.scan, args=(target,))
        thread.start()

    def stop_scan(self):
        self.running = False
        self.status.config(text="Stopping...")

    def scan(self, target):
        mode = self.mode_var.get()
        max_depth = int(self.depth_spin.get())
        same_domain = self.same_domain_var.get()

        self.status.config(text="Scanning...")
        if mode == "web":
            self.scan_web(target, max_depth, same_domain)
        else:
            self.scan_local(target, max_depth)
        self.status.config(text="Scan complete")
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.running = False

    # ---------- Web crawling ----------
    def scan_web(self, start_url, max_depth, same_domain):
        domain = urlparse(start_url).netloc
        visited = set()
        queue = [(start_url, 0)]

        while queue and self.running:
            url, depth = queue.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)
            self.status.config(text=f"Scanning: {url}")
            try:
                resp = requests.get(url, headers=HEADERS, timeout=12)
                html = resp.text
            except Exception as e:
                self.record_broken(url, url, str(e), "web")
                continue

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                full = urljoin(url, href)
                parsed = urlparse(full)

                # Skip mailto and javascript
                if parsed.scheme in ("mailto", "javascript", "tel"):
                    continue

                # Domain restriction
                if same_domain and parsed.netloc and parsed.netloc != domain:
                    continue

                # Enqueue for crawling if same domain or no domain (relative)
                if parsed.scheme in ("http", "https") and full not in visited:
                    queue.append((full, depth + 1))

                # Check link (http(s) or file)
                error = self.check_link_general(full)
                if error:
                    self.record_broken(full, url, error, "web")

    # ---------- Local crawling ----------
    def scan_local(self, target_path, max_depth):
        # Determine whether target is a file or folder
        if os.path.isfile(target_path):
            files = [os.path.abspath(target_path)]
            base_dir = os.path.dirname(os.path.abspath(target_path))
        elif os.path.isdir(target_path):
            base_dir = os.path.abspath(target_path)
            files = []
            for root, _, filenames in os.walk(base_dir):
                for fn in filenames:
                    if fn.lower().endswith((".html", ".htm")):
                        files.append(os.path.join(root, fn))
        else:
            # If user pasted a file:// URL
            if target_path.startswith("file://"):
                local = path_from_file_url(target_path)
                if os.path.isfile(local):
                    files = [os.path.abspath(local)]
                    base_dir = os.path.dirname(os.path.abspath(local))
                elif os.path.isdir(local):
                    base_dir = os.path.abspath(local)
                    files = []
                    for root, _, filenames in os.walk(base_dir):
                        for fn in filenames:
                            if fn.lower().endswith((".html", ".htm")):
                                files.append(os.path.join(root, fn))
                else:
                    self.record_broken(target_path, target_path, "Local path not found", "local")
                    return
            else:
                self.record_broken(target_path, target_path, "Local path not found", "local")
                return

        # Crawl each local HTML file and check links
        visited_local = set()
        for file_path in files:
            if not self.running:
                break
            if file_path in visited_local:
                continue
            visited_local.add(file_path)
            self.status.config(text=f"Scanning local file: {file_path}")
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    html = f.read()
            except Exception as e:
                self.record_broken(file_path, file_path, f"Read error: {e}", "local")
                continue

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = tag["href"].strip()
                if not href:
                    continue
                # Resolve relative links against file location
                if href.startswith("file://"):
                    target = path_from_file_url(href)
                    # absolute file path
                    if not os.path.isabs(target):
                        target = os.path.join(os.path.dirname(file_path), target)
                    target = os.path.normpath(target)
                    if not os.path.exists(target):
                        self.record_broken(target, file_path, "File not found", "local")
                    continue

                parsed = urlparse(href)
                if parsed.scheme in ("http", "https"):
                    # external link from local file
                    error = self.check_link_general(href)
                    if error:
                        self.record_broken(href, file_path, error, "web")
                elif parsed.scheme in ("mailto", "javascript", "tel"):
                    continue
                else:
                    # relative or absolute filesystem path
                    # join with file directory
                    candidate = os.path.join(os.path.dirname(file_path), parsed.path)
                    candidate = os.path.normpath(candidate)
                    if not os.path.exists(candidate):
                        self.record_broken(candidate, file_path, "File not found", "local")

    # ---------- Link checking ----------
    def check_link_general(self, url):
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            return self.check_http(url)
        elif parsed.scheme == "file" or (parsed.scheme == "" and os.path.exists(url)):
            # file URL or raw path
            path = path_from_file_url(url) if parsed.scheme == "file" else url
            if not os.path.isabs(path):
                path = os.path.abspath(path)
            return None if os.path.exists(path) else "File not found"
        else:
            # Unknown scheme treat as OK (or skip)
            return None

    def check_http(self, url):
        try:
            r = requests.head(url, headers=HEADERS, timeout=12, allow_redirects=True)
            if r.status_code < 400:
                return None
            # fallback to GET
            r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
            if r.status_code < 400:
                return None
            return r.status_code
        except Exception as e:
            return str(e)
        finally:
            time.sleep(0.2)

    def record_broken(self, broken, source, error, typ):
        self.results.append({"broken": broken, "source": source, "error": str(error), "type": typ})
        self.tree.insert("", "end", values=(broken, source, str(error), typ))

    # ---------- Export ----------
    def export_csv(self):
        if not self.results:
            messagebox.showinfo("No Data", "No broken links to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files","*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Broken", "Source", "Error", "Type"])
            for r in self.results:
                writer.writerow([r["broken"], r["source"], r["error"], r["type"]])
        messagebox.showinfo("Exported", "CSV export complete.")

    def export_json(self):
        if not self.results:
            messagebox.showinfo("No Data", "No broken links to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files","*.json")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2)
        messagebox.showinfo("Exported", "JSON export complete.")

if __name__ == "__main__":
    root = tk.Tk()
    app = LinkCheckerApp(root)
    root.mainloop()
