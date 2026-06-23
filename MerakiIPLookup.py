import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import csv
import meraki
import winreg
import pickle
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback
import sys
import math
import datetime
import webbrowser

# Used for icon generation (feel free to remove)
import tempfile
import urllib.request
import io
from PIL import Image

REG_PATH = r"Software\MerakiIPFinder"
CACHE_TTL = 3600  # seconds
CACHE_FILENAME = "meraki_cache.pkl"
LOG_FILENAME = "meraki_ipfinder.log"
APP_VERSION = "1.1.0"

APPDATA = os.getenv("APPDATA") or os.getcwd()
CACHE_FILE = os.path.join(APPDATA, CACHE_FILENAME)
LOG_FILE = os.path.join(APPDATA, LOG_FILENAME)

# Tunables
ALLOWED_PRODUCT_TYPES = {"appliance", "wireless", "switch", "camera", "cellular"}  # networks likely to have clients
MAX_WORKERS = 4
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 1.5


# ---------------- Logging ----------------
def log(msg):
	ts = time.strftime("%Y-%m-%d %H:%M:%S")
	line = f"{ts} - {msg}"
	try:
		with open(LOG_FILE, "a", encoding="utf-8") as f:
			f.write(line + "\n")
	except Exception:
		pass
	print(line)


# ---------------- REGISTRY ----------------
def save_settings(api_key, org_id, clear_on_search=False):
	try:
		key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
		winreg.SetValueEx(key, "API_KEY", 0, winreg.REG_SZ, api_key)
		winreg.SetValueEx(key, "ORG_ID", 0, winreg.REG_SZ, org_id)
		winreg.SetValueEx(key, "CLEAR_ON_SEARCH", 0, winreg.REG_SZ, "1" if clear_on_search else "0")
		winreg.CloseKey(key)
	except Exception as e:
		messagebox.showerror("Error", str(e))


def load_settings():
	try:
		key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH)
		api_key = winreg.QueryValueEx(key, "API_KEY")[0]
		org_id = winreg.QueryValueEx(key, "ORG_ID")[0]
		try:
			clear_raw = winreg.QueryValueEx(key, "CLEAR_ON_SEARCH")[0]
			clear_on_search = str(clear_raw).strip().lower() in {"1", "true", "yes", "on"}
		except Exception:
			clear_on_search = True
		winreg.CloseKey(key)
		return api_key, org_id, clear_on_search
	except Exception:
		return "", "", True


# ---------------- TOOLTIP ----------------
class ToolTip:
	def __init__(self, widget, text):
		self.widget = widget
		self.text = text
		widget.bind("<Enter>", self.show)
		widget.bind("<Leave>", self.hide)
		self.tip = None

	def show(self, event):
		self.tip = tk.Toplevel(self.widget)
		self.tip.wm_overrideredirect(True)
		x = self.widget.winfo_rootx() + 20
		y = self.widget.winfo_rooty() + 20
		self.tip.wm_geometry(f"+{x}+{y}")
		tk.Label(self.tip, text=self.text, bg="yellow").pack()

	def hide(self, event):
		if self.tip:
			self.tip.destroy()
			self.tip = None


# ---------------- CACHE HELPERS ----------------
def load_cache():
	if not os.path.exists(CACHE_FILE):
		return None
	try:
		with open(CACHE_FILE, "rb") as f:
			data = pickle.load(f)
		if not isinstance(data, dict):
			return None
		return data
	except Exception as e:
		log(f"Failed to load cache: {e}")
		return None


def save_cache(cache_data):
	try:
		with open(CACHE_FILE, "wb") as f:
			pickle.dump(cache_data, f)
		log(f"Cache saved: {len(cache_data.get('ip_to_network', {}))} IPs, failed_networks={len(cache_data.get('failed_networks', []))}")
	except Exception as e:
		log(f"Failed to save cache: {e}")


def cache_is_valid(cache_data, org_id, ttl=CACHE_TTL):
	if not cache_data:
		return False
	if cache_data.get("org_id") != org_id:
		return False
	ts = cache_data.get("timestamp", 0)
	return (time.time() - ts) < ttl


# ---------------- RETRY HELPERS ----------------
def retry_call(func, attempts=RETRY_ATTEMPTS, backoff_base=RETRY_BACKOFF_BASE):
	"""
	Generic retry wrapper. func is a callable that returns (success, result_or_exception).
	"""
	last_exc = None
	for attempt in range(1, attempts + 1):
		try:
			return True, func()
		except Exception as e:
			last_exc = e
			wait = backoff_base ** (attempt - 1)
			log(f"Retry {attempt}/{attempts} failed: {e}. Backing off {wait:.1f}s")
			time.sleep(wait)
	return False, last_exc



def set_app_icon(root, url):
	try:
		temp_dir = os.getenv("TEMP") or tempfile.gettempdir()
		ico_path = os.path.join(temp_dir, "temp_icon.ico")

		# If we already have a good icon, just use it
		if os.path.exists(ico_path) and os.path.getsize(ico_path) > 0:
			try:
				root.iconbitmap(ico_path)
				return
			except Exception:
				os.remove(ico_path)  # corrupted, force re-download

		# Download icon bytes
		req = urllib.request.Request(
			url,
			headers={"User-Agent": "Mozilla/5.0"}
		)

		with urllib.request.urlopen(req, timeout=15) as response:
			data = response.read()
			content_type = response.headers.get("Content-Type", "").lower()

		# Detect format
		is_png = ("png" in content_type) or data.startswith(b"\x89PNG")
		is_ico = ("image/x-icon" in content_type or "image/vnd.microsoft.icon" in content_type)

		if is_ico:
			# Save ICO directly
			with open(ico_path, "wb") as f:
				f.write(data)

		elif is_png:
			# Convert PNG -> ICO
			img = Image.open(io.BytesIO(data))
			img.save(ico_path, format="ICO")

		else:
			raise ValueError(f"Unsupported icon format: {content_type}")

		# Apply icon
		root.iconbitmap(ico_path)

	except Exception as e:
		print(f"Failed to set app icon: {e}")



# ---------------- MAIN APP ----------------
class App:
	def __init__(self, root):
		self.root = root
		self.root.title("Meraki IP Finder")
		self.root.geometry("980x580")
		set_app_icon(self.root, "https://meraki.cisco.com/wp-content/themes/genesis-meraki/images/meraki-favicon-120x120.png")

		self.api_key, self.org_id, self.clear_on_search = load_settings()
		self.dashboard = None

		self.ip_to_network = {}
		self.results = []
		self.cache_build_lock = threading.Lock()
		self.clear_on_search_var = tk.BooleanVar(value=self.clear_on_search)

		self.create_ui()
		self.root.protocol("WM_DELETE_WINDOW", self.on_close)

	# -------- UI --------
	def create_ui(self):
		self.create_menu()

		top_frame = tk.Frame(self.root)
		top_frame.pack(pady=10, fill=tk.X)

		self.entry = tk.Entry(top_frame, width=44, fg="gray")
		self.entry.pack(side=tk.LEFT, padx=5)

		self.placeholder = "Enter IP"
		self.entry.insert(0, self.placeholder)

		# Bind events
		self.entry.bind("<FocusIn>", self._clear_placeholder)
		self.entry.bind("<FocusOut>", self._add_placeholder)
		self.entry.bind("<Return>", lambda event: self.search_single())

		tk.Button(top_frame, text="Search", command=self.search_single).pack(side=tk.LEFT, padx=5)
		tk.Button(top_frame, text="Import CSV", command=self.import_csv).pack(side=tk.LEFT, padx=5)

		self.progress = ttk.Progressbar(self.root, orient="horizontal", length=930, mode="determinate")
		self.progress.pack(pady=10)

		self.status = tk.Label(self.root, text="Ready")
		self.status.pack()

		self.tree = ttk.Treeview(
			self.root,
			columns=("IP", "Network", "DeviceName", "MAC", "LastSeen"),
			show="headings"
		)
		self.tree.heading("IP", text="IP Address")
		self.tree.heading("Network", text="Network")
		self.tree.heading("DeviceName", text="Device Name")
		self.tree.heading("MAC", text="MAC Address")
		self.tree.heading("LastSeen", text="Client Last Seen")

		self.tree.column("IP", width=140, anchor="w")
		self.tree.column("Network", width=260, anchor="w")
		self.tree.column("DeviceName", width=200, anchor="w")
		self.tree.column("MAC", width=150, anchor="w")
		self.tree.column("LastSeen", width=170, anchor="w")

		self.tree.pack(fill=tk.BOTH, expand=True, pady=10)

		bottom_frame = tk.Frame(self.root)
		bottom_frame.pack(pady=5)
		tk.Button(bottom_frame, text="Export Results", command=self.export_csv).pack(side=tk.LEFT, padx=5)
		tk.Button(bottom_frame, text="Clear Cache Now", command=self.clear_cache_now).pack(side=tk.LEFT, padx=5)

	def create_menu(self):
		menubar = tk.Menu(self.root)

		file_menu = tk.Menu(menubar, tearoff=0)
		file_menu.add_command(label="Import CSV", command=self.import_csv)
		file_menu.add_command(label="Export Results", command=self.export_csv)
		file_menu.add_separator()
		file_menu.add_command(label="Exit", command=self.on_close)
		menubar.add_cascade(label="File", menu=file_menu)

		tools_menu = tk.Menu(menubar, tearoff=0)
		tools_menu.add_command(label="Settings", command=self.open_settings)
		tools_menu.add_command(label="Clear Cache Now", command=self.clear_cache_now)
		menubar.add_cascade(label="Tools", menu=tools_menu)

		options_menu = tk.Menu(menubar, tearoff=0)
		options_menu.add_checkbutton(
			label="Clear results before search",
			variable=self.clear_on_search_var,
			command=self.on_toggle_clear_on_search
		)
		menubar.add_cascade(label="Options", menu=options_menu)

		help_menu = tk.Menu(menubar, tearoff=0)
		help_menu.add_command(label="About", command=self.show_about)
		menubar.add_cascade(label="Help", menu=help_menu)

		self.root.config(menu=menubar)

	def on_toggle_clear_on_search(self):
		self.clear_on_search = bool(self.clear_on_search_var.get())
		save_settings(self.api_key, self.org_id, self.clear_on_search)
		log(f"Clear on search set to {self.clear_on_search}")

	def show_about(self):
		win = tk.Toplevel(self.root)
		win.title("About")
		win.geometry("420x220")
		win.resizable(False, False)

		frame = tk.Frame(win, padx=16, pady=16)
		frame.pack(fill=tk.BOTH, expand=True)

		tk.Label(frame, text="Meraki IP Finder", font=("Segoe UI", 14, "bold")).pack(anchor="w")
		tk.Label(frame, text=f"Version {APP_VERSION}").pack(anchor="w", pady=(4, 10))

		tk.Label(
			frame,
			text="Search Meraki client IPs by network,\nhttps://github.com/gogeekery/Python-Scripts.",
			justify="left"
		).pack(anchor="w")

		tk.Label(frame, text=f"Cache file: {CACHE_FILE}", justify="left", fg="gray").pack(anchor="w", pady=(12, 0))

		btn_frame = tk.Frame(frame)
		btn_frame.pack(anchor="w", pady=(16, 0))

		tk.Button(
			btn_frame,
			text="Visit GitHub",
			command=lambda: webbrowser.open_new_tab("https://github.com/gogeekery/Python-Scripts")
		).pack(side=tk.LEFT)

		tk.Button(
			btn_frame,
			text="Close",
			command=win.destroy
		).pack(side=tk.LEFT, padx=8)

	def _clear_placeholder(self, event):
		if self.entry.get() == self.placeholder:
			self.entry.delete(0, tk.END)
			self.entry.config(fg="black")

	def _add_placeholder(self, event):
		if not self.entry.get():
			self.entry.insert(0, self.placeholder)
			self.entry.config(fg="gray")

	# -------- SETTINGS --------
	def open_settings(self):
		win = tk.Toplevel(self.root)
		win.title("Settings")
		win.geometry("420x300")

		tk.Label(win, text="API Key").pack(pady=(10, 0))
		api_entry = tk.Entry(win, width=50)
		api_entry.pack()
		api_entry.insert(0, self.api_key)

		ToolTip(api_entry, "Find in Meraki Dashboard:\nOrganization → Settings → Dashboard API")

		tk.Label(win, text="Organization ID").pack(pady=(10, 0))
		org_entry = tk.Entry(win, width=50)
		org_entry.pack()
		org_entry.insert(0, self.org_id)

		ToolTip(org_entry, "Find via API or dashboard URL\n(e.g., /o/{orgId}/overview)")

		clear_checkbox = tk.Checkbutton(
			win,
			text="Clear results before each search",
			variable=self.clear_on_search_var
		)
		clear_checkbox.pack(pady=10)

		def save():
			self.api_key = api_entry.get().strip()
			self.org_id = org_entry.get().strip()
			self.clear_on_search = bool(self.clear_on_search_var.get())
			save_settings(self.api_key, self.org_id, self.clear_on_search)
			messagebox.showinfo("Saved", "Settings saved successfully")
			win.destroy()

		def refresh_cache():
			try:
				if os.path.exists(CACHE_FILE):
					os.remove(CACHE_FILE)
				with self.cache_build_lock:
					self.ip_to_network = {}
				messagebox.showinfo("Cache", "Cache cleared. Next search will rebuild cache.")
			except Exception as e:
				messagebox.showerror("Error", f"Failed to clear cache: {e}")

		tk.Button(win, text="Save", command=save).pack(pady=6)
		tk.Button(win, text="Refresh Cache", command=refresh_cache).pack(pady=6)

	# -------- SEARCH HELPERS --------
	def _extract_device_name(self, client):
		"""
		Meraki client name fields can vary depending on client/network type.
		Try a few likely fields and return a friendly fallback.
		"""
		candidate_keys = [
			"description",
			"dhcpHostname",
			"recentDeviceName",
			"deviceName",
			"mdnsName",
			"hostname",
			"user"
		]
		for key in candidate_keys:
			value = client.get(key)
			if value is not None:
				text = str(value).strip()
				if text:
					return text
		return "N/A"

	def _extract_mac(self, client):
		return (
			client.get("mac")
			or client.get("clientMac")
			or client.get("macAddress")
			or "N/A"
		)

	# -------- SEARCH LOGIC --------
	def init_dashboard(self):
		if not self.api_key or not self.org_id:
			messagebox.showerror("Error", "Please configure API key and Org ID in Settings")
			return False
		if not self.dashboard:
			try:
				self.dashboard = meraki.DashboardAPI(self.api_key, suppress_logging=True)
			except Exception as e:
				messagebox.showerror("Error", f"Failed to initialize Meraki Dashboard API:\n{e}")
				return False
		return True

	def search_single(self):
		ip = self.entry.get().strip()

		if ip == self.placeholder or not ip:
			messagebox.showerror("Input Error", "Please enter an IP address.")
			return

		self.run_search([ip])

	def import_csv(self):
		file_path = filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
		if not file_path:
			return

		ips = []
		with open(file_path, newline="", encoding="utf-8-sig") as f:
			reader = csv.reader(f)
			for row in reader:
				if row and row[0].strip():
					ips.append(row[0].strip())

		if not ips:
			messagebox.showinfo("No IPs", "No IP addresses found in CSV.")
			return

		self.run_search(ips)

	def run_search(self, ip_list):
		thread = threading.Thread(target=self.search_task, args=(ip_list,), daemon=True)
		thread.start()

	def build_ip_table_if_needed(self):
		with self.cache_build_lock:
			cache = load_cache()
			if cache_is_valid(cache, self.org_id):
				self.ip_to_network = cache.get("ip_to_network", {})
				log(f"Loaded cache with {len(self.ip_to_network)} IPs")
				return True

			self.update_status("Loading networks...")
			try:
				networks = self.dashboard.organizations.getOrganizationNetworks(self.org_id, total_pages="all")
			except Exception as e:
				messagebox.showerror("Error", f"Failed to load networks:\n{e}")
				log(f"Failed to load networks: {e}")
				return False

			total_networks = len(networks)
			if total_networks == 0:
				messagebox.showinfo("No Networks", "No networks found for this organization.")
				return False

			self.progress["maximum"] = total_networks
			self.progress["value"] = 0

			# ip_map: ip -> {
			#   "network": name,
			#   "lastSeen": ts,
			#   "active": bool,
			#   "deviceName": str,
			#   "mac": str
			# }
			ip_map = {}
			failed_networks = []
			per_network_counts = {}

			def _normalize_last_seen(client):
				for key in ("lastSeen", "seenTime", "lastSeenAt", "last_seen"):
					v = client.get(key)
					if v:
						try:
							return float(v)
						except Exception:
							try:
								# Handle ISO-ish strings like 2025-01-01T12:34:56.000000Z
								raw = str(v).split(".")[0].replace("Z", "")
								return time.mktime(time.strptime(raw, "%Y-%m-%dT%H:%M:%S"))
							except Exception:
								pass
				return 0.0

			def _is_client_network(net):
				pts = net.get("productTypes") or []
				return any(p in ALLOWED_PRODUCT_TYPES for p in pts)

			def _fetch_clients_for_network_with_retries(net):
				net_id = net.get("id")
				net_name = net.get("name", net_id)

				if not _is_client_network(net):
					log(f"[SKIP] {net_name} ({net_id}) skipped due to productTypes={net.get('productTypes')}")
					return net_id, net_name, [], None

				attempt = 0
				while attempt < RETRY_ATTEMPTS:
					attempt += 1
					try:
						clients = self.dashboard.networks.getNetworkClients(net_id, total_pages="all", perPage=1000)
						return net_id, net_name, clients or [], None
					except meraki.exceptions.APIError as api_err:
						msg = str(api_err)
						retry_after = None
						try:
							if hasattr(api_err, "response") and api_err.response is not None:
								retry_after = api_err.response.headers.get("Retry-After")
						except Exception:
							retry_after = None

						if "400 Bad Request" in msg and "Invalid device type" in msg:
							tb = traceback.format_exc()
							return net_id, net_name, None, tb

						if "429" in msg or "Too Many Requests" in msg:
							wait = float(retry_after) if retry_after else (RETRY_BACKOFF_BASE ** (attempt - 1))
							log(f"[RATE] {net_name} ({net_id}) rate-limited; attempt {attempt}/{RETRY_ATTEMPTS}, sleeping {wait:.1f}s")
							time.sleep(wait)
							continue

						wait = (RETRY_BACKOFF_BASE ** (attempt - 1))
						log(f"[RETRY] {net_name} ({net_id}) attempt {attempt}/{RETRY_ATTEMPTS} failed: {msg}. Backing off {wait:.1f}s")
						time.sleep(wait)
						continue
					except Exception as e:
						wait = (RETRY_BACKOFF_BASE ** (attempt - 1))
						log(f"[ERROR] {net_name} ({net_id}) attempt {attempt}/{RETRY_ATTEMPTS} exception: {e}. Backing off {wait:.1f}s")
						time.sleep(wait)
						continue

				tb = traceback.format_exc()
				return net_id, net_name, None, tb

			# Parallel fetch phase
			workers = min(MAX_WORKERS, max(2, total_networks))
			with ThreadPoolExecutor(max_workers=workers) as ex:
				futures = {ex.submit(_fetch_clients_for_network_with_retries, net): net for net in networks}
				completed = 0
				for fut in as_completed(futures):
					completed += 1
					try:
						net_id, net_name, clients, err = fut.result()
						if err:
							failed_networks.append((net_id, net_name, err))
							log(f"[ERROR] {net_name} ({net_id}) failed after retries: {err}")
						else:
							count = len(clients) if clients is not None else 0
							per_network_counts[net_id] = (net_name, count)
							log(f"[INFO] {net_name} ({net_id}) -> {count} clients")

							# Merge clients into ip_map preferring active then newest lastSeen
							for client in clients:
								ip = client.get("ip")
								if not ip:
									continue

								active = bool(client.get("active") or client.get("isActive") or client.get("status") == "Active")
								last_seen = _normalize_last_seen(client)
								device_name = self._extract_device_name(client)
								mac = self._extract_mac(client)

								new_entry = {
									"network": net_name,
									"lastSeen": last_seen,
									"active": active,
									"deviceName": device_name,
									"mac": mac
								}

								existing = ip_map.get(ip)
								if not existing:
									ip_map[ip] = new_entry
									continue

								if not existing.get("active", False) and active:
									ip_map[ip] = new_entry
									continue

								if last_seen > existing.get("lastSeen", 0):
									ip_map[ip] = new_entry
									continue
					except Exception as e:
						failed_networks.append((None, None, traceback.format_exc()))
						log(f"[ERROR] Future exception: {e}")

					self.progress["value"] = completed
					self.update_status(f"Building cache {completed}/{total_networks}")
					try:
						self.root.update_idletasks()
					except Exception:
						pass

			# Sequential fallback for any networks that still failed
			if failed_networks:
				log(f"{len(failed_networks)} networks failed in parallel phase. Attempting sequential fallback.")
				sequential_attempts = []
				for net_id, net_name, err in failed_networks:
					net_obj = next((n for n in networks if n.get("id") == net_id), None)
					if not net_obj:
						continue
					net_id2, net_name2, clients2, err2 = _fetch_clients_for_network_with_retries(net_obj)
					if err2:
						sequential_attempts.append((net_id2, net_name2, err2))
						log(f"[ERROR] Sequential fallback failed for {net_name2} ({net_id2}): {err2}")
					else:
						count = len(clients2) if clients2 else 0
						per_network_counts[net_id2] = (net_name2, count)
						log(f"[INFO] Sequential fallback success {net_name2} ({net_id2}) -> {count} clients")
						for client in clients2:
							ip = client.get("ip")
							if not ip:
								continue

							active = bool(client.get("active") or client.get("isActive") or client.get("status") == "Active")
							last_seen = _normalize_last_seen(client)
							device_name = self._extract_device_name(client)
							mac = self._extract_mac(client)

							new_entry = {
								"network": net_name2,
								"lastSeen": last_seen,
								"active": active,
								"deviceName": device_name,
								"mac": mac
							}

							existing = ip_map.get(ip)
							if not existing:
								ip_map[ip] = new_entry
								continue
							if not existing.get("active", False) and active:
								ip_map[ip] = new_entry
								continue
							if last_seen > existing.get("lastSeen", 0):
								ip_map[ip] = new_entry
								continue

				failed_networks = sequential_attempts

			# Decide whether to save cache
			if failed_networks and os.path.exists(CACHE_FILE):
				existing = load_cache()
				if existing and cache_is_valid(existing, self.org_id):
					self.ip_to_network = existing.get("ip_to_network", {})
					messagebox.showwarning("Partial Cache Build", f"Cache build had {len(failed_networks)} failures. Using existing cache on disk.")
					log("Using existing cache because new build had failures.")
					return True
				else:
					cache_data = {
						"org_id": self.org_id,
						"timestamp": time.time(),
						"ip_to_network": ip_map,
						"failed_networks": [(n[0], n[1]) for n in failed_networks],
						"complete": False,
					}
					save_cache(cache_data)
					self.ip_to_network = ip_map
					messagebox.showwarning("Partial Cache Saved", f"Cache built with {len(ip_map)} IPs but {len(failed_networks)} networks failed. You can Refresh Cache later.")
					return True

			# No failures (or none remaining) — save full cache
			cache_data = {
				"org_id": self.org_id,
				"timestamp": time.time(),
				"ip_to_network": ip_map,
				"failed_networks": [],
				"complete": True,
			}
			save_cache(cache_data)
			self.ip_to_network = ip_map
			log(f"Cache build complete: {len(ip_map)} IPs across {total_networks} networks")
			return True

	def search_task(self, ip_list):
		if not self.init_dashboard():
			return

		ok = self.build_ip_table_if_needed()
		if not ok:
			return

		# Optional clear-on-search behavior
		if self.clear_on_search:
			self.tree.delete(*self.tree.get_children())
			self.results = []

		total = len(ip_list)
		self.progress["maximum"] = total
		self.progress["value"] = 0

		# Append if clear-on-search is disabled
		results = list(self.results)

		for i, ip in enumerate(ip_list, start=1):
			self.update_status(f"Resolving {ip} ({i}/{total})")

			entry = self.ip_to_network.get(ip)

			# Backward compatibility: older cache may store just a string network name
			if isinstance(entry, str):
				found_network = entry
				device_name = "N/A"
				mac = "N/A"
				last_seen_str = "N/A"
			elif isinstance(entry, dict) and entry:
				found_network = entry.get("network", "Not Found")
				device_name = entry.get("deviceName", "N/A") or "N/A"
				mac = entry.get("mac", "N/A") or "N/A"
				last_seen_ts = entry.get("lastSeen", 0) or 0

				if last_seen_ts:
					try:
						last_seen_str = datetime.datetime.fromtimestamp(float(last_seen_ts)).strftime("%Y-%m-%d %H:%M:%S")
					except Exception:
						last_seen_str = str(last_seen_ts)
				else:
					last_seen_str = "N/A"
			else:
				found_network = "Not Found"
				device_name = "N/A"
				mac = "N/A"
				last_seen_str = "N/A"

			row = (ip, found_network, device_name, mac, last_seen_str)
			results.append(row)
			self.tree.insert("", "end", values=row)

			self.progress["value"] = i
			try:
				self.root.update_idletasks()
			except Exception:
				pass

		self.results = results
		self.update_status("Complete")

	def update_status(self, text):
		try:
			self.status.config(text=text)
		except Exception:
			pass

	# -------- EXPORT --------
	def export_csv(self):
		if not hasattr(self, "results") or not self.results:
			messagebox.showinfo("No results", "No results to export.")
			return

		file_path = filedialog.asksaveasfilename(defaultextension=".csv")
		if not file_path:
			return

		try:
			with open(file_path, "w", newline="", encoding="utf-8") as f:
				writer = csv.writer(f)
				writer.writerow(["IP", "Network", "Device Name", "MAC", "Last Seen"])
				writer.writerows(self.results)
			messagebox.showinfo("Exported", "CSV exported successfully")
		except Exception as e:
			messagebox.showerror("Error", f"Failed to export CSV: {e}")

	# -------- Cache controls & exit --------
	def clear_cache_now(self):
		try:
			if os.path.exists(CACHE_FILE):
				os.remove(CACHE_FILE)
			with self.cache_build_lock:
				self.ip_to_network = {}
			messagebox.showinfo("Cache", "Cache cleared.")
			log("Cache cleared by user.")
		except Exception as e:
			messagebox.showerror("Error", f"Failed to clear cache: {e}")
			log(f"Failed to clear cache: {e}")

	def on_close(self):
		"""
		Clear cache file on app exit as requested, then exit.
		"""
		try:
			if os.path.exists(CACHE_FILE):
				os.remove(CACHE_FILE)
				log("Cache file removed on exit.")
		except Exception as e:
			log(f"Failed to remove cache on exit: {e}")
		try:
			self.root.destroy()
		except Exception:
			sys.exit(0)


# -------- RUN --------
if __name__ == "__main__":
	try:
		os.makedirs(APPDATA, exist_ok=True)
	except Exception:
		pass

	root = tk.Tk()
	app = App(root)
	root.mainloop()
