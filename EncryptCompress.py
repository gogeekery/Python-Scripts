#!/usr/bin/env python3
import tkinter as tk
from tkinter import filedialog, messagebox
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Protocol.KDF import scrypt
import gzip, bz2, lzma, os, struct

# Constants
HEADER_MAGIC = b'ENC1'
SALT_LEN = 16
NONCE_LEN = 12
KEY_LEN = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

COMP_METHODS = {
    'none': 0,
    'gzip': 1,
    'bz2': 2,
    'lzma': 3
}
REV_COMP = {v:k for k,v in COMP_METHODS.items()}

def compress_bytes(data, method):
    if method == 'gzip': return gzip.compress(data)
    if method == 'bz2': return bz2.compress(data)
    if method == 'lzma': return lzma.compress(data)
    return data

def decompress_bytes(data, method):
    if method == 'gzip': return gzip.decompress(data)
    if method == 'bz2': return bz2.decompress(data)
    if method == 'lzma': return lzma.decompress(data)
    return data

def derive_key(password, salt):
    return scrypt(password.encode('utf-8'), salt, KEY_LEN, N=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)

def encrypt_file(in_path, out_path, password, comp):
    with open(in_path, 'rb') as f:
        plaintext = f.read()
    compressed = compress_bytes(plaintext, comp)
    salt = get_random_bytes(SALT_LEN)
    key = derive_key(password, salt)
    nonce = get_random_bytes(NONCE_LEN)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(compressed)
    # Header: MAGIC | salt | nonce | tag_len(1) | tag | comp_flag(1) | ciphertext_len(8)
    with open(out_path, 'wb') as out:
        out.write(HEADER_MAGIC)
        out.write(salt)
        out.write(nonce)
        out.write(struct.pack('B', len(tag)))
        out.write(tag)
        out.write(struct.pack('B', COMP_METHODS[comp]))
        out.write(struct.pack('>Q', len(ciphertext)))
        out.write(ciphertext)

def decrypt_file(in_path, out_path, password):
    with open(in_path, 'rb') as f:
        magic = f.read(4)
        if magic != HEADER_MAGIC:
            raise ValueError("Not a supported encrypted file")
        salt = f.read(SALT_LEN)
        nonce = f.read(NONCE_LEN)
        tag_len = struct.unpack('B', f.read(1))[0]
        tag = f.read(tag_len)
        comp_flag = struct.unpack('B', f.read(1))[0]
        c_len = struct.unpack('>Q', f.read(8))[0]
        ciphertext = f.read(c_len)
    key = derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    compressed = cipher.decrypt_and_verify(ciphertext, tag)
    comp = REV_COMP.get(comp_flag, 'none')
    plaintext = decompress_bytes(compressed, comp)
    with open(out_path, 'wb') as out:
        out.write(plaintext)

# --- GUI ---
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("File Encryptor")
        self.geometry("480x220")
        tk.Label(self, text="Password:").pack(anchor='w', padx=10, pady=(10,0))
        self.pw = tk.Entry(self, show='*', width=40)
        self.pw.pack(padx=10)
        tk.Label(self, text="Compression:").pack(anchor='w', padx=10, pady=(10,0))
        self.comp_var = tk.StringVar(value='none')
        for opt in COMP_METHODS.keys():
            tk.Radiobutton(self, text=opt, variable=self.comp_var, value=opt).pack(anchor='w', padx=20)
        tk.Button(self, text="Encrypt file", command=self.encrypt_action).pack(side='left', padx=20, pady=10)
        tk.Button(self, text="Decrypt file", command=self.decrypt_action).pack(side='right', padx=20, pady=10)

    def encrypt_action(self):
        infile = filedialog.askopenfilename(title="Select file to encrypt")
        if not infile: return
        outfile = filedialog.asksaveasfilename(title="Save encrypted file as", defaultextension=".enc")
        if not outfile: return
        try:
            encrypt_file(infile, outfile, self.pw.get(), self.comp_var.get())
            messagebox.showinfo("Done", f"Encrypted to {outfile}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def decrypt_action(self):
        infile = filedialog.askopenfilename(title="Select file to decrypt", filetypes=[("Encrypted files","*.enc"),("All files","*.*")])
        if not infile: return
        outfile = filedialog.asksaveasfilename(title="Save decrypted file as")
        if not outfile: return
        try:
            decrypt_file(infile, outfile, self.pw.get())
            messagebox.showinfo("Done", f"Decrypted to {outfile}")
        except Exception as e:
            messagebox.showerror("Error", str(e))



if __name__ == "__main__":
    App().mainloop()
