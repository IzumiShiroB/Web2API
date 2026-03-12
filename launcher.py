import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import sys
import os
import signal
import json
import time
from pathlib import Path

from server_state import (
    set_server_running, set_server_stopped, request_shutdown, 
    is_server_running, get_server_info, init_state, STATE_FILE
)


class ServerLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Web Proxy Server Launcher")
        self.root.geometry("600x500")
        self.root.resizable(True, True)
        
        self.server_process = None
        self.is_running = False
        self.browser_monitor_thread = None
        self.state_monitor_thread = None
        
        if is_server_running():
            request_shutdown()
            time.sleep(2)
        
        if self.check_port_in_use():
            self.kill_port_process()
        
        init_state()
        
        self.setup_ui()
        self.load_platforms()
        self.start_state_monitor()
        
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        platform_frame = ttk.LabelFrame(main_frame, text="Platform Selection", padding="10")
        platform_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(platform_frame, text="Select Platform:").pack(side=tk.LEFT, padx=(0, 10))
        
        self.platform_var = tk.StringVar(value="deepseek")
        self.platform_combo = ttk.Combobox(platform_frame, textvariable=self.platform_var, state="readonly", width=30)
        self.platform_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.start_btn = ttk.Button(control_frame, text="Start Server", command=self.start_server)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.stop_btn = ttk.Button(control_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        self.status_var = tk.StringVar(value="Status: Stopped")
        self.status_label = ttk.Label(control_frame, textvariable=self.status_var, foreground="gray")
        self.status_label.pack(side=tk.RIGHT)
        
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.auto_stop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            options_frame, 
            text="Auto-stop server when browser is closed", 
            variable=self.auto_stop_var
        ).pack(anchor=tk.W)
        
        log_frame = ttk.LabelFrame(main_frame, text="Server Log", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def start_state_monitor(self):
        self.state_monitor_thread = threading.Thread(target=self._monitor_server_state, daemon=True)
        self.state_monitor_thread.start()
    
    def _monitor_server_state(self):
        last_running = False
        while True:
            try:
                current_running = is_server_running()
                
                if last_running and not current_running:
                    self.root.after(0, self._on_server_stopped)
                
                last_running = current_running
                
            except Exception as e:
                pass
            
            time.sleep(1)
    
    def _on_server_stopped(self):
        if self.is_running:
            self.log("Server stopped (detected via state)")
            self.is_running = False
            self.server_process = None
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.platform_combo.config(state="readonly")
            self.status_var.set("Status: Stopped")
            self.status_label.config(foreground="gray")
        
    def load_platforms(self):
        platforms_dir = Path(__file__).parent / "platforms"
        platforms = []
        
        if platforms_dir.exists():
            for file in platforms_dir.glob("*.py"):
                if file.name != "__init__.py" and file.name != "base.py":
                    platform_name = file.stem
                    platforms.append(platform_name)
        
        if platforms:
            self.platform_combo['values'] = platforms
            self.platform_var.set(platforms[0])
        else:
            self.platform_combo['values'] = ["deepseek"]
            self.platform_var.set("deepseek")
    
    def check_port_in_use(self, port=23456):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(('127.0.0.1', port)) == 0
    
    def kill_port_process(self, port=23456):
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            
            for line in result.stdout.split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        subprocess.run(
                            ['taskkill', '/F', '/PID', pid],
                            capture_output=True,
                            creationflags=subprocess.CREATE_NO_WINDOW
                        )
                        time.sleep(1)
                        return True
        except Exception:
            pass
        return False
    
    def log(self, message):
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
    
    def start_server(self):
        if self.is_running:
            return
        
        if self.check_port_in_use():
            self.log("Error: Port 23456 is already in use!")
            messagebox.showerror("Error", "Port 23456 is already in use.\nPlease close the existing server first.")
            return
        
        platform = self.platform_var.get()
        self.log(f"Starting server with platform: {platform}")
        
        env = os.environ.copy()
        env["SELECTED_PLATFORM"] = platform
        
        self.server_process = subprocess.Popen(
            [sys.executable, "main.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=Path(__file__).parent,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        )
        
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.platform_combo.config(state=tk.DISABLED)
        self.status_var.set("Status: Running")
        self.status_label.config(foreground="green")
        
        threading.Thread(target=self.read_output, daemon=True).start()
        threading.Thread(target=self.monitor_process, daemon=True).start()
        
        if self.auto_stop_var.get():
            self.browser_monitor_thread = threading.Thread(target=self.monitor_browser, daemon=True)
            self.browser_monitor_thread.start()
        
        self.log("Server started successfully!")
    
    def stop_server(self):
        if not self.is_running:
            return
        
        self.log("Requesting server shutdown...")
        request_shutdown()
        
        self.is_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.platform_combo.config(state="readonly")
        self.status_var.set("Status: Stopped")
        self.status_label.config(foreground="gray")
        
        if self.server_process:
            try:
                self.server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.log("Force killing server process...")
                self.server_process.kill()
            self.server_process = None
    
    def read_output(self):
        while self.is_running and self.server_process:
            try:
                line = self.server_process.stdout.readline()
                if line:
                    self.root.after(0, self.log, line.rstrip())
                elif self.server_process.poll() is not None:
                    break
            except Exception:
                break
    
    def monitor_process(self):
        while self.is_running and self.server_process:
            ret = self.server_process.poll()
            if ret is not None:
                self.root.after(0, self.on_server_exit, ret)
                break
            time.sleep(0.5)
    
    def monitor_browser(self):
        self.log("Browser monitor started (will auto-stop on browser close)")
        
        while self.is_running:
            try:
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq msedge.exe'],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                
                if "msedge.exe" not in result.stdout:
                    self.log("Browser closed detected, stopping server...")
                    self.root.after(0, self.stop_server)
                    break
                
            except Exception as e:
                pass
            
            time.sleep(2)
    
    def on_server_exit(self, return_code):
        if self.is_running:
            self.log(f"Server exited with code: {return_code}")
            self.is_running = False
            self.server_process = None
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.platform_combo.config(state="readonly")
            self.status_var.set("Status: Stopped (Unexpected)")
            self.status_label.config(foreground="red")
    
    def on_closing(self):
        if self.is_running:
            if messagebox.askokcancel("Quit", "Server is still running. Stop and quit?"):
                self.log("Requesting server shutdown...")
                request_shutdown()
                
                for i in range(10):
                    if not is_server_running():
                        self.log("Server stopped.")
                        break
                    time.sleep(0.5)
                else:
                    self.log("Server shutdown timeout, forcing close...")
                
                self.root.destroy()
        else:
            self.root.destroy()

def main():
    root = tk.Tk()
    app = ServerLauncher(root)
    root.mainloop()

if __name__ == "__main__":
    main()
