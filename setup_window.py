"""Visible Windows bootstrap window for the browser setup wizard."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from urllib.request import urlopen
from urllib.parse import urlencode
import webbrowser

ROOT = Path(__file__).resolve().parent


def main():
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print("OwnDesk: this Python installation has no Tkinter. Opening console setup instead.", flush=True)
        return subprocess.call([sys.executable, str(ROOT / "launcher.py"), "--open-browser"])

    window = tk.Tk()
    window.title("OwnDesk — настройка и запуск")
    window.geometry("740x460")
    window.minsize(660, 440)
    window.configure(bg="#0b1220")
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure("OwnDesk.Horizontal.TProgressbar", troughcolor="#17243a", background="#568cff", bordercolor="#17243a")
    events = queue.Queue()
    process = None
    origin = None
    target = None
    opened = False
    closing = False

    panel = tk.Frame(window, bg="#111d30", padx=32, pady=26)
    panel.pack(fill="both", expand=True, padx=20, pady=20)
    tk.Label(panel, text="OwnDesk", font=("Segoe UI", 27, "bold"), bg="#111d30", fg="#f0f5ff").pack(anchor="w")
    tk.Label(panel, text="Подготовка и запуск мастера настройки", font=("Segoe UI", 13), bg="#111d30", fg="#a8b9d2").pack(anchor="w", pady=(8, 24))
    title = tk.StringVar(value="Подготавливаем окружение…")
    tk.Label(panel, textvariable=title, font=("Segoe UI", 15, "bold"), bg="#111d30", fg="#f0f5ff").pack(anchor="w")
    progress = ttk.Progressbar(panel, mode="indeterminate", style="OwnDesk.Horizontal.TProgressbar")
    progress.pack(fill="x", pady=18)
    progress.start(15)
    detail = tk.StringVar(value="Первый запуск устанавливает зависимости. Мастер автоматически откроется в браузере после готовности сервера.")
    tk.Label(panel, textvariable=detail, font=("Segoe UI", 10), justify="left", wraplength=610, bg="#111d30", fg="#a8b9d2").pack(anchor="w", fill="x")
    address = tk.StringVar(value="")
    tk.Label(panel, textvariable=address, font=("Segoe UI", 11), bg="#111d30", fg="#87afff").pack(anchor="w", pady=(14, 8))
    buttons = tk.Frame(panel, bg="#111d30")
    buttons.pack(side="bottom", fill="x", pady=(20, 0))

    def button(text, action, primary=False):
        widget = tk.Button(buttons, text=text, command=action, font=("Segoe UI", 10), padx=14, pady=9,
                           bg="#3766d6" if primary else "#24344f", fg="white", activebackground="#4276ed",
                           activeforeground="white", relief="flat", cursor="hand2")
        widget.pack(side="left", padx=(0, 9))
        return widget

    open_button = button("Открыть мастер", lambda: webbrowser.open(target) if target else None, True)
    open_button.configure(state="disabled")

    def open_log():
        path = ROOT / ".runtime/startup.log"
        if path.exists():
            os.startfile(str(path))

    button("Открыть журнал", open_log)

    def start():
        nonlocal process, origin, target, opened
        origin = target = None
        opened = False
        open_button.configure(state="disabled")
        retry_button.configure(state="disabled")
        title.set("Подготавливаем окружение…")
        detail.set("Мастер откроется автоматически. При первом запуске нужны интернет и несколько минут на установку зависимостей.")
        address.set("")
        progress.start(15)
        python = Path(sys.executable).with_name("python.exe")
        process = subprocess.Popen([str(python), str(ROOT / "launcher.py")], cwd=ROOT,
                                   env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        child = process

        def read_output():
            for line in child.stdout:
                events.put(("line", line.strip()))
            events.put(("exit", child.wait()))
            child.stdout.close()

        threading.Thread(target=read_output, daemon=True).start()

    retry_button = button("Повторить", start)
    retry_button.configure(state="disabled")

    def check_ready(url, child):
        try:
            with urlopen(url + "/api/v1/health/live", timeout=2) as response:
                if response.status != 200:
                    return
            with urlopen(url + "/api/v1/auth/options", timeout=2) as response:
                options = json.load(response)
            destination = url
            code_file = ROOT / ".runtime/installation-code.txt"
            if options.get("setup_required") and code_file.exists():
                destination += "/#" + urlencode({"setup": code_file.read_text().strip()})
            events.put(("ready", (child, destination)))
        except (OSError, ValueError):
            events.put(("not_ready", child))

    polling = False

    def tick():
        nonlocal origin, target, opened, polling
        if closing:
            return
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == "line":
                if value.startswith("OWNDESK_URL="):
                    origin = value.removeprefix("OWNDESK_URL=")
                    address.set(origin)
                    title.set("Запускаем сервер…")
                    detail.set("Ждём готовности сервера. Затем мастер автоматически откроется в браузере.")
                elif "creating Python environment" in value:
                    title.set("Подготавливаем Python…")
                    detail.set("Создаём отдельное окружение для этой установки OwnDesk.")
                elif "installing dependencies" in value:
                    title.set("Устанавливаем зависимости…")
                    detail.set("Скачиваем и устанавливаем компоненты. Первый запуск может занять несколько минут.")
                elif "preparing database" in value:
                    title.set("Подготавливаем базу данных…")
                    detail.set("Подготавливаем данные этой установки. Подробности доступны в журнале.")
            elif kind == "ready":
                child, destination = value
                polling = False
                if child is process and child.poll() is None and not opened:
                    target = destination
                    opened = True
                    progress.stop()
                    title.set("Мастер настройки готов")
                    detail.set("Мастер открыт в браузере. Оставьте это окно открытым во время работы. Кнопка ниже откроет мастер повторно.")
                    open_button.configure(state="normal")
                    webbrowser.open(target)
            elif kind == "not_ready":
                polling = False
            elif kind == "exit":
                progress.stop()
                title.set("Сервер остановлен" if value == 0 else "Не удалось запустить OwnDesk")
                detail.set("Откройте журнал, чтобы увидеть подробности, или нажмите «Повторить». Рабочие серверы других установок не останавливались.")
                open_button.configure(state="disabled")
                retry_button.configure(state="normal")
                origin = None
        if origin and not opened and not polling and process and process.poll() is None:
            polling = True
            threading.Thread(target=check_ready, args=(origin, process), daemon=True).start()
        window.after(400, tick)

    def close():
        nonlocal closing
        closing = True
        if process and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", close)
    window.after(100, start)
    window.after(200, tick)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
