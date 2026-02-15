# https://github.com/gogeekery/Python-Scripts
# Crawls documents in a SharePoint site to find all links

# pip install msal requests python-docx PyPDF2 pandas

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import re
import os
import io
import json
import time
import requests
import msal
from urllib.parse import urlparse
from docx import Document
import PyPDF2
import pandas as pd
from pathlib import Path

# ----------------- Defaults / Config -----------------
DEFAULT_SITE = ""
CONFIG_PATH = Path.home() / ".sharepoint_link_extractor_config.json"

# ----------------- GUI Class -----------------
class SharePointLinkExtractorGUI:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SharePoint Link Extractor")
        self.root.geometry("980x760")

        self.stop_flag = False
        self.total_files_scanned = 0
        self.total_links_found = 0
        self.links = []  # list of dicts: {'site','file_name','file_web_url','found_url'}

        # Regex matches http(s)://... and www.... (non-clickable links)
        self.url_pattern = re.compile(r'(https?://[^\s<>")]+|www\.[^\s<>")]+)', re.IGNORECASE)

        self._build_ui()
        self._load_config()

    # ---------------- UI ----------------
    def _build_ui(self):
        header = ttk.Label(self.root, text="SharePoint Link Extractor", font=("Segoe UI", 20, "bold"))
        header.pack(pady=12)

        conn_frame = ttk.LabelFrame(self.root, text="Connection / App Credentials")
        conn_frame.pack(fill="x", padx=20, pady=8)

        ttk.Label(conn_frame, text="Site URL:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.site_entry = ttk.Entry(conn_frame, width=80)
        self.site_entry.insert(0, DEFAULT_SITE)
        self.site_entry.grid(row=0, column=1, padx=8, pady=6, columnspan=3)

        ttk.Label(conn_frame, text="Tenant ID:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.tenant_entry = ttk.Entry(conn_frame, width=36)
        self.tenant_entry.grid(row=1, column=1, padx=8, pady=6)

        ttk.Label(conn_frame, text="Client ID:").grid(row=1, column=2, sticky="w", padx=8, pady=6)
        self.client_entry = ttk.Entry(conn_frame, width=36)
        self.client_entry.grid(row=1, column=3, padx=8, pady=6)

        ttk.Label(conn_frame, text="Client Secret:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.secret_entry = ttk.Entry(conn_frame, width=80, show="*")
        self.secret_entry.grid(row=2, column=1, padx=8, pady=6, columnspan=3)

        # Save config button
        self.save_config_btn = ttk.Button(conn_frame, text="Save Config", command=self._save_config)
        self.save_config_btn.grid(row=3, column=1, sticky="w", padx=8, pady=6)

        self.clear_config_btn = ttk.Button(conn_frame, text="Clear Saved Config", command=self._clear_config)
        self.clear_config_btn.grid(row=3, column=2, sticky="w", padx=8, pady=6)

        # File type toggles
        toggles_frame = ttk.Frame(self.root)
        toggles_frame.pack(fill="x", padx=20, pady=6)

        self.scan_docx_var = tk.BooleanVar(value=True)
        self.scan_pdf_var = tk.BooleanVar(value=True)
        self.docx_check = ttk.Checkbutton(toggles_frame, text="Scan DOCX", variable=self.scan_docx_var)
        self.docx_check.pack(side="left", padx=6)
        self.pdf_check = ttk.Checkbutton(toggles_frame, text="Scan PDF", variable=self.scan_pdf_var)
        self.pdf_check.pack(side="left", padx=6)

        # Control Frame
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill="x", padx=20, pady=8)

        self.start_button = ttk.Button(control_frame, text="Start Scan", command=self.start_scan)
        self.start_button.pack(side="left", padx=6)

        self.stop_button = ttk.Button(control_frame, text="Stop", command=self.stop_scan, state="disabled")
        self.stop_button.pack(side="left", padx=6)

        self.export_button = ttk.Button(control_frame, text="Export CSV", command=self.export_csv, state="disabled")
        self.export_button.pack(side="left", padx=6)

        self.status_label = ttk.Label(control_frame, text="Status: Idle", foreground="blue")
        self.status_label.pack(side="right")

        # Progress Frame
        progress_frame = ttk.LabelFrame(self.root, text="Progress")
        progress_frame.pack(fill="x", padx=20, pady=8)

        # Determinate progress bar: we will set maximum after counting files
        self.progress = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=6)

        self.progress_label = ttk.Label(progress_frame, text="Files Scanned: 0 | Links Found: 0 | Total Files: 0")
        self.progress_label.pack(padx=10, pady=6)

        # Log Frame
        log_frame = ttk.LabelFrame(self.root, text="Activity Log")
        log_frame.pack(fill="both", expand=True, padx=20, pady=8)

        self.log_text = tk.Text(log_frame, wrap="none")
        self.log_text.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def run(self):
        self.root.mainloop()

    # ---------------- Config ----------------
    def _load_config(self):
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.site_entry.delete(0, "end")
                self.site_entry.insert(0, cfg.get("site_url", ""))
                self.tenant_entry.delete(0, "end")
                self.tenant_entry.insert(0, cfg.get("tenant_id", ""))
                self.client_entry.delete(0, "end")
                self.client_entry.insert(0, cfg.get("client_id", ""))
                self.secret_entry.delete(0, "end")
                self.secret_entry.insert(0, cfg.get("client_secret", ""))
                self._log("Loaded saved config.\n")
        except Exception as e:
            self._log(f"Failed to load config: {e}\n")

    def _save_config(self):
        cfg = {
            "site_url": self.site_entry.get().strip(),
            "tenant_id": self.tenant_entry.get().strip(),
            "client_id": self.client_entry.get().strip(),
            "client_secret": self.secret_entry.get().strip()
        }
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            self._log(f"Config saved to {CONFIG_PATH}\n")
            messagebox.showinfo("Saved", "Configuration saved locally.")
        except Exception as e:
            self._log(f"Failed to save config: {e}\n")
            messagebox.showerror("Error", f"Failed to save config: {e}")

    def _clear_config(self):
        try:
            if CONFIG_PATH.exists():
                CONFIG_PATH.unlink()
            self._log("Saved config cleared.\n")
            messagebox.showinfo("Cleared", "Saved configuration cleared.")
        except Exception as e:
            self._log(f"Failed to clear config: {e}\n")
            messagebox.showerror("Error", f"Failed to clear config: {e}")

    # ---------------- START / STOP ----------------
    def start_scan(self):
        site = self.site_entry.get().strip()
        tenant = self.tenant_entry.get().strip()
        client_id = self.client_entry.get().strip()
        client_secret = self.secret_entry.get().strip()
        scan_docx = self.scan_docx_var.get()
        scan_pdf = self.scan_pdf_var.get()

        if not site or not tenant or not client_id or not client_secret:
            messagebox.showerror("Missing Info", "Please provide Site URL, Tenant ID, Client ID, and Client Secret.")
            return
        if not (scan_docx or scan_pdf):
            messagebox.showerror("No File Types", "Enable at least one file type to scan (DOCX or PDF).")
            return

        self.stop_flag = False
        self.total_files_scanned = 0
        self.total_links_found = 0
        self.links = []

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.export_button.config(state="disabled")
        self._update_status("Preparing scan…")

        threading.Thread(
            target=self._scan_worker,
            args=(site, tenant, client_id, client_secret, scan_docx, scan_pdf),
            daemon=True
        ).start()

    def stop_scan(self):
        self.stop_flag = True
        self._log("Stop requested...\n")
        self._update_status("Stopping...")

    # ---------------- Logging / UI helpers ----------------
    def _log(self, text):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {text}")
        self.log_text.see("end")

    def _update_progress(self, total_files=0):
        self.progress_label.config(text=f"Files Scanned: {self.total_files_scanned} | Links Found: {self.total_links_found} | Total Files: {total_files}")
        # update progress bar value
        try:
            self.progress['value'] = self.total_files_scanned
        except Exception:
            pass

    def _update_status(self, text):
        self.status_label.config(text=f"Status: {text}")

    # ---------------- Graph / MSAL helpers ----------------
    def _acquire_token(self, tenant_id, client_id, client_secret, scope=["https://graph.microsoft.com/.default"]):
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=client_secret)
        result = app.acquire_token_for_client(scopes=scope)
        if "access_token" in result:
            return result["access_token"]
        else:
            raise Exception(f"Token acquisition failed: {result.get('error_description') or result}")

    def _get_site_resource(self, access_token, site_url):
        parsed = urlparse(site_url)
        hostname = parsed.netloc
        path = parsed.path.rstrip('/')
        if not path:
            path = '/'
        graph_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:{path}"
        headers = {"Authorization": f"Bearer {access_token}"}
        r = requests.get(graph_url, headers=headers)
        if r.status_code == 200:
            return r.json()
        else:
            raise Exception(f"Failed to resolve site: {r.status_code} {r.text}")

    def _list_drive_children(self, access_token, drive_id, item_id=None):
        headers = {"Authorization": f"Bearer {access_token}"}
        if item_id:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children"
        else:
            url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"

        while url:
            r = requests.get(url, headers=headers)
            if r.status_code != 200:
                raise Exception(f"List children failed: {r.status_code} {r.text}")
            data = r.json()
            for item in data.get("value", []):
                yield item
            url = data.get("@odata.nextLink")

    def _download_drive_item(self, access_token, drive_id, item_id):
        headers = {"Authorization": f"Bearer {access_token}"}
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"
        r = requests.get(url, headers=headers, stream=True)
        if r.status_code in (200, 302):
            return r.content
        else:
            raise Exception(f"Download failed: {r.status_code} {r.text}")

    # ---------------- Count target files (for progress bar) ----------------
    def _count_target_files(self, access_token, drive_id, scan_docx, scan_pdf):
        count = 0
        stack = [None]
        while stack and not self.stop_flag:
            current = stack.pop()
            for item in self._list_drive_children(access_token, drive_id, current):
                if self.stop_flag:
                    break
                item_name = item.get("name", "").lower()
                item_id = item.get("id")
                if item.get("folder"):
                    stack.append(item_id)
                elif item.get("file"):
                    if (scan_docx and item_name.endswith(".docx")) or (scan_pdf and item_name.endswith(".pdf")):
                        count += 1
        return count

    # ---------------- File parsing ----------------
    def _extract_links_from_docx_bytes(self, b):
        links = set()
        try:
            doc = Document(io.BytesIO(b))
            # paragraphs and runs
            for p in doc.paragraphs:
                text = p.text or ""
                for match in self.url_pattern.findall(text):
                    links.add(match.rstrip('.,;:'))

            # tables
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for match in self.url_pattern.findall(cell.text or ""):
                            links.add(match.rstrip('.,;:'))

            # Extract hyperlinks stored in relationships (these are often not visible as plain text)
            try:
                rels = doc.part.rels
                for rel in rels.values():
                    if rel.reltype == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink":
                        target = rel.target_ref
                        if target:
                            links.add(target.rstrip('.,;:'))
            except Exception:
                pass

        except Exception:
            # fallback: try to decode and regex
            try:
                text = b.decode('utf-8', errors='ignore')
                for match in self.url_pattern.findall(text):
                    links.add(match.rstrip('.,;:'))
            except Exception:
                pass
        return list(links)

    def _extract_links_from_pdf_bytes(self, b):
        links = set()
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(b))
            for page in reader.pages:
                try:
                    text = page.extract_text() or ""
                    for match in self.url_pattern.findall(text):
                        links.add(match.rstrip('.,;:'))
                except Exception:
                    continue
            # Also check annotations for link URIs (some PDFs store links as annotations)
            try:
                for page in reader.pages:
                    annots = page.get("/Annots")
                    if annots:
                        for a in annots:
                            obj = a.get_object()
                            if "/A" in obj and "/URI" in obj["/A"]:
                                uri = obj["/A"]["/URI"]
                                if uri:
                                    links.add(uri.rstrip('.,;:'))
            except Exception:
                pass
        except Exception:
            # fallback decode
            try:
                text = b.decode('utf-8', errors='ignore')
                for match in self.url_pattern.findall(text):
                    links.add(match.rstrip('.,;:'))
            except Exception:
                pass
        return list(links)

    # ---------------- Crawl worker ----------------
    def _scan_worker(self, site_url, tenant_id, client_id, client_secret, scan_docx, scan_pdf):
        total_files = 0
        try:
            token = self._acquire_token(tenant_id, client_id, client_secret)
            self._update_status("Token acquired")
            self._log("Token acquired.\n")

            site = self._get_site_resource(token, site_url)
            site_id = site.get("id")
            self._log(f"Resolved site id: {site_id}\n")

            headers = {"Authorization": f"Bearer {token}"}
            drive_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive"
            r = requests.get(drive_url, headers=headers)
            if r.status_code != 200:
                raise Exception(f"Failed to get drive: {r.status_code} {r.text}")
            drive = r.json()
            drive_id = drive.get("id")
            self._log(f"Using drive id: {drive_id}\n")

            # First pass: count files to scan
            self._update_status("Counting target files...")
            self._log("Counting files to scan (this may take a moment)...\n")
            total_files = self._count_target_files(token, drive_id, scan_docx, scan_pdf)
            self._log(f"Total target files: {total_files}\n")
            self.progress['maximum'] = max(1, total_files)
            self._update_progress(total_files=total_files)

            # Second pass: traverse and download
            self._update_status(f"Scanning (0/{total_files})")
            stack = [None]
            while stack and not self.stop_flag:
                current = stack.pop()
                for item in self._list_drive_children(token, drive_id, current):
                    if self.stop_flag:
                        break
                    item_name = item.get("name")
                    item_id = item.get("id")
                    item_folder = item.get("folder")
                    item_file = item.get("file")
                    web_url = item.get("webUrl", "")
                    if item_folder:
                        stack.append(item_id)
                    elif item_file:
                        lower = (item_name or "").lower()
                        is_docx = lower.endswith(".docx")
                        is_pdf = lower.endswith(".pdf")
                        if (is_docx and scan_docx) or (is_pdf and scan_pdf):
                            self._log(f"Downloading: {web_url}\n")
                            try:
                                content = self._download_drive_item(token, drive_id, item_id)
                                self.total_files_scanned += 1
                                found = []
                                if is_docx:
                                    found = self._extract_links_from_docx_bytes(content)
                                elif is_pdf:
                                    found = self._extract_links_from_pdf_bytes(content)

                                for u in found:
                                    self.links.append({
                                        "site": site_url,
                                        "file_name": item_name,
                                        "file_web_url": web_url,
                                        "found_url": u
                                    })
                                self.total_links_found += len(found)
                                self._log(f"Scanned {item_name}: {len(found)} links found\n")
                                self._update_progress(total_files=total_files)
                                self._update_status(f"Scanning ({self.total_files_scanned}/{total_files})")
                            except Exception as ex:
                                self._log(f"Error downloading/parsing {item_name}: {ex}\n")
                    # else: ignore other types
                # end for children
            # end while

            if self.stop_flag:
                self._update_status("Stopped by user")
                self._log("Scan stopped by user.\n")
            else:
                self._update_status("Completed")
                self._log("Scan completed.\n")

        except Exception as e:
            self._log(f"Fatal error: {e}\n")
            self._update_status("Error")
        finally:
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            if self.links:
                self.export_button.config(state="normal")
            # ensure progress label updated with final totals
            self._update_progress(total_files=total_files)

    # ---------------- Export ----------------
    def export_csv(self):
        if not self.links:
            messagebox.showinfo("No Data", "No links to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files","*.csv")])
        if not path:
            return
        df = pd.DataFrame(self.links)
        df.to_csv(path, index=False)
        self._log(f"Exported {len(self.links)} rows to {path}\n")
        messagebox.showinfo("Exported", f"Exported {len(self.links)} rows to {path}")

# ----------------- Run -----------------
if __name__ == "__main__":
    app = SharePointLinkExtractorGUI()
    app.run()
