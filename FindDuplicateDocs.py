import os
import itertools
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz
from docx import Document

# Docx Duplicate Finder
# Select a folder, set similarity threshold
# and generate an csv report of duplicates and similarity %

# pip install python-docx pandas rapidfuzz openpyxl



def save_file_dialog(ftypes=[("All files", "*.*")]):

    # Create a hidden root window
    root = tk.Tk()
    root.withdraw() 

    # Open the "save as" dialog
    file_path = filedialog.asksaveasfilename(
        defaultextension=ftypes[0],
        filetypes=ftypes,
        title="Save File As"
    )

    # Destroy the hidden root window after the dialog is closed
    root.destroy()

    return file_path


def get_docx_text(path: Path) -> str:
    try:
        doc = Document(path)
        paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paras)
    except Exception as e:
        print(f"Warning: could not read {path}: {e}")
        return ""

def collect_docx_paths(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.docx") if p.is_file()]

def find_duplicates(paths: list[Path], threshold: int) -> list[tuple]:
    texts = {p: get_docx_text(p) for p in paths}
    dupes = []
    for a, b in itertools.combinations(paths, 2):
        t1, t2 = texts[a], texts[b]
        if not t1 or not t2:
            continue
        score = fuzz.ratio(t1, t2)
        if score >= threshold:
            dupes.append((a, b, score))
    return dupes

def write_to_csv(dupes: list[tuple], outpath: Path, show_full: bool):
    # transform to strings based on show_full flag
    rows = []
    for a, b, score in dupes:
        fa = str(a) if show_full else a.name
        fb = str(b) if show_full else b.name
        rows.append((fa, fb, score))

    df = pd.DataFrame(rows, columns=["Source File", "Duplicate", "Similarity (%)"])
    df.to_csv(outpath, index=False)



# ---- UI ----------

class DuplicateFinderApp:
    def __init__(self, root):
        self.root = root
        root.title("DOCX Duplicate Finder")
        root.resizable(False, False)

        # Folder selector
        tk.Label(root, text="Folder:").grid(row=0, column=0, sticky="e", padx=5, pady=5)
        self.folder_var = tk.StringVar()
        tk.Entry(root, textvariable=self.folder_var, width=40).grid(row=0, column=1, padx=5)
        tk.Button(root, text="Browse…", command=self.browse_folder).grid(row=0, column=2, padx=5)

        # Threshold
        tk.Label(root, text="Threshold (%):").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.thresh_var = tk.IntVar(value=90)
        tk.Entry(root, textvariable=self.thresh_var, width=5).grid(row=1, column=1, sticky="w")

        # Show full path checkbox
        self.fullpath_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            root,
            text="Show full paths",
            variable=self.fullpath_var
        ).grid(row=2, column=1, sticky="w", pady=5)

        # Run button
        self.run_btn = tk.Button(root, text="Start", width=10, command=self.on_run)
        self.run_btn.grid(row=3, column=1, pady=10)

        # Status label
        self.status_var = tk.StringVar(value="Select a folder to scan (subfolders will be scanned too)")
        tk.Label(root, textvariable=self.status_var, fg="blue").grid(
            row=4, column=0, columnspan=3, pady=5
        )

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select folder to scan")
        if folder:
            self.folder_var.set(folder)

    def on_run(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return
        thresh = self.thresh_var.get()
        if not (0 <= thresh <= 100):
            messagebox.showerror("Error", "Threshold must be between 0 and 100.")
            return

        # disable UI while scanning
        self.run_btn.config(state="disabled")
        self.status_var.set("Scanning…")
        threading.Thread(
            target=self.run_scan,
            args=(folder, thresh, self.fullpath_var.get()),
            daemon=True
        ).start()

    def run_scan(self, folder, thresh, show_full):
        try:
            rootp = Path(folder)
            paths = collect_docx_paths(rootp)
            self.status_var.set(f"Found {len(paths)} files. Comparing...")

            dupes = find_duplicates(paths, thresh)
            outpath = save_file_dialog([("CSV files", "*.csv")])
            if outpath:
                write_to_csv(dupes, outpath, show_full)
            else:
                return

            msg = (
                f"Done! {len(dupes)} pairs ≥{thresh}% similar.\n"
                f"Report saved to:\n{outpath}"
            )
            messagebox.showinfo("Finished", msg)
            self.status_var.set("Finished.")
        except Exception as e:
            messagebox.showerror("Error", f"An error occurred:\n{e}")
            self.status_var.set("Error.")
        finally:
            self.run_btn.config(state="normal")


if __name__ == "__main__":
    root = tk.Tk()
    app = DuplicateFinderApp(root)
    root.mainloop()
