#!/usr/bin/env python3
"""
smart_photo_organizer.py
Scans a directory and all subfolders for image files
Finds near-duplicate images using perceptual hashing, organizes by EXIF date
Moves duplicates to separate folder, and writes an undo log and JSON report.

pip install pillow imagehash piexif
"""

import json
import os
import shutil
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import traceback

import imagehash
from PIL import Image, ImageTk, ExifTags
import piexif
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tiff', '.bmp', '.gif', '.webp', '.heic'}

# ---------- Core logic (same ideas as CLI) ----------

def iter_images(root):
    for p in Path(root).rglob('*'):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            yield p

def get_exif_date(path):
    try:
        img = Image.open(path)
        exif = img._getexif()
        if not exif:
            return None
        for tag, val in exif.items():
            name = ExifTags.TAGS.get(tag, tag)
            if name in ('DateTimeOriginal', 'DateTime'):
                try:
                    return datetime.strptime(val, '%Y:%m:%d %H:%M:%S')
                except Exception:
                    pass
    except Exception:
        pass
    return None

def phash(path, hash_size=16):
    try:
        with Image.open(path) as im:
            return imagehash.phash(im, hash_size=hash_size)
    except Exception:
        return None

def hamming(a, b):
    return (a - b)

def ensure_dir(p):
    Path(p).mkdir(parents=True, exist_ok=True)

# ---------- Worker that computes groups ----------

def compute_groups(source, threshold=6, hash_size=16, progress_callback=None, stop_event=None):
    files = list(iter_images(source))
    total = len(files)
    if progress_callback:
        progress_callback(f'Found {total} images')
    hashes = {}
    for i, p in enumerate(files, 1):
        if stop_event and stop_event.is_set():
            return None
        h = phash(p, hash_size=hash_size)
        if h is not None:
            hashes[p] = h
        if progress_callback:
            progress_callback(f'Hashing {i}/{total}: {p.name}')
    unassigned = set(hashes.keys())
    groups = []
    while unassigned:
        if stop_event and stop_event.is_set():
            return None
        base = unassigned.pop()
        group = [base]
        to_check = set(unassigned)
        for other in to_check:
            if hamming(hashes[base], hashes[other]) <= threshold:
                group.append(other)
                unassigned.remove(other)
        groups.append(sorted(group, key=lambda p: p.stat().st_size, reverse=True))
    # sort groups by size (largest group first)
    groups.sort(key=lambda g: (-len(g), -g[0].stat().st_size))
    return groups

# ---------- File operations (organize & quarantine) ----------

def organize_and_quarantine(groups, dest_root, quarantine_root, dry_run=True, report_path=None, undo_log_path=None, progress_callback=None, stop_event=None):
    report = {'timestamp': datetime.now().isoformat(), 'groups': []}
    undo_actions = []
    for idx, grp in enumerate(groups, 1):
        if stop_event and stop_event.is_set():
            break
        if len(grp) == 1:
            src = grp[0]
            date = get_exif_date(src) or datetime.fromtimestamp(src.stat().st_mtime)
            sub = Path(dest_root) / f'{date.year}' / f'{date.month:02d}-{date.day:02d}'
            dst = sub / src.name
            report['groups'].append({'type': 'unique', 'kept': str(src), 'moved_to': str(dst)})
            if not dry_run:
                ensure_dir(sub)
                shutil.move(str(src), str(dst))
                undo_actions.append({'action': 'move', 'src': str(dst), 'dst': str(src)})
            if progress_callback:
                progress_callback(f'[{idx}/{len(groups)}] Unique: {src.name}')
        else:
            kept = grp[0]
            kept_date = get_exif_date(kept) or datetime.fromtimestamp(kept.stat().st_mtime)
            kept_sub = Path(dest_root) / f'{kept_date.year}' / f'{kept_date.month:02d}-{kept_date.day:02d}'
            kept_dst = kept_sub / kept.name
            duplicates = grp[1:]
            dup_targets = []
            for d in duplicates:
                qsub = Path(quarantine_root) / datetime.now().strftime('%Y%m%d_%H%M%S')
                qdst = qsub / d.name
                dup_targets.append({'src': str(d), 'quarantine': str(qdst)})
            report['groups'].append({'type': 'duplicates', 'kept': str(kept), 'kept_moved_to': str(kept_dst), 'duplicates': dup_targets})
            if not dry_run:
                ensure_dir(kept_sub)
                shutil.move(str(kept), str(kept_dst))
                undo_actions.append({'action': 'move', 'src': str(kept_dst), 'dst': str(kept)})
                for d in duplicates:
                    qsub = Path(quarantine_root) / datetime.now().strftime('%Y%m%d_%H%M%S')
                    ensure_dir(qsub)
                    qdst = qsub / d.name
                    shutil.move(str(d), str(qdst))
                    undo_actions.append({'action': 'move', 'src': str(qdst), 'dst': str(d)})
            if progress_callback:
                progress_callback(f'[{idx}/{len(groups)}] Duplicates: kept {kept.name}, moved to duplicates {len(duplicates)}')
    if report_path:
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
    if undo_log_path and not dry_run:
        with open(undo_log_path, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': datetime.now().isoformat(), 'actions': undo_actions}, f, indent=2)
    return report, undo_actions

def undo_from_log(undo_log_path, progress_callback=None):
    with open(undo_log_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    actions = data.get('actions', [])
    for act in reversed(actions):
        if act['action'] == 'move':
            src = Path(act['src'])
            dst = Path(act['dst'])
            if src.exists():
                ensure_dir(dst.parent)
                if progress_callback:
                    progress_callback(f'Restoring {src.name}')
                shutil.move(str(src), str(dst))
            else:
                if progress_callback:
                    progress_callback(f'Warning: {src} not found, skipping.')

# ---------- GUI ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Photo Deduplicator & Organizer')
        self.geometry('1000x700')
        self.resizable(True, True)
        self._build_ui()
        self.groups = []
        self.thumb_cache = {}
        self.worker_thread = None
        self.stop_event = threading.Event()

    def _build_ui(self):
        frm = ttk.Frame(self)
        frm.pack(fill='both', expand=True, padx=8, pady=8)

        # Top controls
        top = ttk.Frame(frm)
        top.pack(fill='x', pady=4)

        ttk.Label(top, text='Source:').grid(row=0, column=0, sticky='w')
        self.src_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.src_var, width=60).grid(row=0, column=1, sticky='we', padx=4)
        ttk.Button(top, text='Browse', command=self.browse_source).grid(row=0, column=2, padx=4)

        ttk.Label(top, text='Destination:').grid(row=1, column=0, sticky='w')
        self.dest_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.dest_var, width=60).grid(row=1, column=1, sticky='we', padx=4)
        ttk.Button(top, text='Browse', command=self.browse_dest).grid(row=1, column=2, padx=4)

        ttk.Label(top, text='Duplicates:').grid(row=2, column=0, sticky='w')
        self.quar_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.quar_var, width=60).grid(row=2, column=1, sticky='we', padx=4)
        ttk.Button(top, text='Browse', command=self.browse_quarantine).grid(row=2, column=2, padx=4)

        # Options
        opts = ttk.Frame(frm)
        opts.pack(fill='x', pady=6)
        ttk.Label(opts, text='Similarity threshold:').grid(row=0, column=0, sticky='w')
        self.threshold = tk.IntVar(value=6)
        ttk.Scale(opts, from_=0, to=16, variable=self.threshold, orient='horizontal').grid(row=0, column=1, sticky='we', padx=6)
        ttk.Label(opts, textvariable=self.threshold).grid(row=0, column=2, sticky='w', padx=6)

        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text='Dry run (do not move files)', variable=self.dry_run_var).grid(row=0, column=3, padx=8)

        # Buttons
        btns = ttk.Frame(frm)
        btns.pack(fill='x', pady=6)
        self.scan_btn = ttk.Button(btns, text='Scan', command=self.start_scan)
        self.scan_btn.pack(side='left', padx=4)
        self.stop_btn = ttk.Button(btns, text='Stop', command=self.stop_worker, state='disabled')
        self.stop_btn.pack(side='left', padx=4)
        self.run_btn = ttk.Button(btns, text='Organize & Dedupe', command=self.start_organize, state='disabled')
        self.run_btn.pack(side='left', padx=4)
        self.undo_btn = ttk.Button(btns, text='Undo (from log)', command=self.undo_action)
        self.undo_btn.pack(side='left', padx=4)

        # Progress / status
        self.status = tk.StringVar(value='Ready')
        ttk.Label(frm, textvariable=self.status).pack(fill='x', pady=4)

        # Main panes: groups list and preview
        panes = ttk.Panedwindow(frm, orient='horizontal')
        panes.pack(fill='both', expand=True)

        left = ttk.Frame(panes, width=350)
        panes.add(left, weight=1)
        right = ttk.Frame(panes)
        panes.add(right, weight=3)

        # Groups list
        ttk.Label(left, text='Groups (click to inspect)').pack(anchor='w')
        self.group_list = tk.Listbox(left, height=30)
        self.group_list.pack(fill='both', expand=True, padx=4, pady=4)
        self.group_list.bind('<<ListboxSelect>>', self.on_group_select)

        # Preview area
        preview_top = ttk.Frame(right)
        preview_top.pack(fill='x')
        ttk.Label(preview_top, text='Group preview').pack(anchor='w')
        self.canvas = tk.Canvas(right, bg='#222', height=480)
        self.canvas.pack(fill='both', expand=True, padx=4, pady=4)
        self.info_text = tk.Text(right, height=6)
        self.info_text.pack(fill='x', padx=4, pady=4)

    # ---------- UI callbacks ----------

    def browse_source(self):
        d = filedialog.askdirectory()
        if d:
            self.src_var.set(d)
            # default dest/quarantine
            if not self.dest_var.get():
                self.dest_var.set(str(Path(d) / 'organized'))
            if not self.quar_var.get():
                self.quar_var.set(str(Path(d) / 'quarantine'))

    def browse_dest(self):
        d = filedialog.askdirectory()
        if d:
            self.dest_var.set(d)

    def browse_quarantine(self):
        d = filedialog.askdirectory()
        if d:
            self.quar_var.set(d)

    def start_scan(self):
        src = self.src_var.get()
        if not src:
            messagebox.showerror('Error', 'Please select a source folder.')
            return
        self.scan_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.run_btn.config(state='disabled')
        self.status.set('Starting scan...')
        self.group_list.delete(0, tk.END)
        self.canvas.delete('all')
        self.info_text.delete('1.0', tk.END)
        self.thumb_cache.clear()
        self.groups = []
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._scan_worker, args=(src, self.threshold.get()))
        self.worker_thread.start()

    def stop_worker(self):
        self.stop_event.set()
        self.status.set('Stopping...')

    def _scan_worker(self, src, threshold):
        try:
            def progress(msg):
                self.status.set(msg)
            groups = compute_groups(src, threshold=threshold, progress_callback=progress, stop_event=self.stop_event)
            if groups is None:
                self.status.set('Scan stopped.')
                self.scan_btn.config(state='normal')
                self.stop_btn.config(state='disabled')
                return
            self.groups = groups
            self.after(0, self._populate_group_list)
            self.status.set(f'Scan complete: {len(groups)} groups found.')
            self.scan_btn.config(state='normal')
            self.stop_btn.config(state='disabled')
            self.run_btn.config(state='normal')
        except Exception as e:
            traceback.print_exc()
            self.status.set(f'Error during scan: {e}')
            self.scan_btn.config(state='normal')
            self.stop_btn.config(state='disabled')

    def _populate_group_list(self):
        self.group_list.delete(0, tk.END)
        for i, g in enumerate(self.groups, 1):
            label = f'Group {i}: {len(g)} file(s) — {g[0].name}'
            self.group_list.insert(tk.END, label)

    def on_group_select(self, evt):
        sel = self.group_list.curselection()
        if not sel:
            return
        idx = sel[0]
        grp = self.groups[idx]
        self.show_group_preview(grp)

    def show_group_preview(self, grp):
        self.canvas.delete('all')
        self.info_text.delete('1.0', tk.END)
        # create thumbnails horizontally
        x = 10
        y = 10
        max_h = 0
        for p in grp:
            try:
                thumb = self._get_thumb(p, max_size=(200, 200))
                img_id = self.canvas.create_image(x, y, anchor='nw', image=thumb)
                # keep reference
                self.canvas.image = getattr(self.canvas, 'image', []) + [thumb]
                # label
                size_kb = p.stat().st_size // 1024
                self.canvas.create_text(x, y + 205, anchor='nw', text=f'{p.name}\n{size_kb} KB', fill='white', width=200)
                x += 220
                max_h = max(max_h, 220)
            except Exception:
                continue
        # info
        info_lines = []
        info_lines.append(f'Files in group: {len(grp)}')
        for p in grp:
            info_lines.append(f'- {p.name} ({p.stat().st_size // 1024} KB)  {p}')
        self.info_text.insert('1.0', '\n'.join(info_lines))

    def _get_thumb(self, path, max_size=(200, 200)):
        key = (str(path), max_size)
        if key in self.thumb_cache:
            return self.thumb_cache[key]
        with Image.open(path) as im:
            im.thumbnail(max_size)
            tkimg = ImageTk.PhotoImage(im.convert('RGBA'))
            self.thumb_cache[key] = tkimg
            return tkimg

    def start_organize(self):
        if not self.groups:
            messagebox.showerror('Error', 'No groups to process. Run Scan first.')
            return
        dest = self.dest_var.get()
        quar = self.quar_var.get()
        if not dest or not quar:
            messagebox.showerror('Error', 'Please select destination and duplicates folders.')
            return
        dry_run = self.dry_run_var.get()
        report_path = Path.cwd() / 'organize_report_gui.json'
        undo_log_path = Path.cwd() / 'organize_undo_gui.json'
        self.scan_btn.config(state='disabled')
        self.run_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.status.set('Starting organize...')
        self.stop_event.clear()
        self.worker_thread = threading.Thread(target=self._organize_worker, args=(dest, quar, dry_run, str(report_path), str(undo_log_path)))
        self.worker_thread.start()

    def _organize_worker(self, dest, quar, dry_run, report_path, undo_log_path):
        try:
            def progress(msg):
                self.status.set(msg)
            report, undo_actions = organize_and_quarantine(self.groups, dest, quar, dry_run=dry_run, report_path=report_path, undo_log_path=undo_log_path, progress_callback=progress, stop_event=self.stop_event)
            if self.stop_event.is_set():
                self.status.set('Operation stopped.')
            else:
                self.status.set(f'Operation complete. Report: {report_path}')
            self.scan_btn.config(state='normal')
            self.run_btn.config(state='normal')
            self.stop_btn.config(state='disabled')
        except Exception as e:
            traceback.print_exc()
            self.status.set(f'Error during organize: {e}')
            self.scan_btn.config(state='normal')
            self.run_btn.config(state='normal')
            self.stop_btn.config(state='disabled')

    def undo_action(self):
        path = filedialog.askopenfilename(title='Select undo log JSON', filetypes=[('JSON files', '*.json'), ('All files', '*.*')])
        if not path:
            return
        self.stop_event.clear()
        self.scan_btn.config(state='disabled')
        self.run_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.worker_thread = threading.Thread(target=self._undo_worker, args=(path,))
        self.worker_thread.start()

    def _undo_worker(self, path):
        try:
            def progress(msg):
                self.status.set(msg)
            undo_from_log(path, progress_callback=progress)
            self.status.set('Undo complete.')
        except Exception as e:
            traceback.print_exc()
            self.status.set(f'Error during undo: {e}')
        finally:
            self.scan_btn.config(state='normal')
            self.run_btn.config(state='normal')
            self.stop_btn.config(state='disabled')

# ---------- Run ----------

def main():
    app = App()
    app.mainloop()

if __name__ == '__main__':
    main()
