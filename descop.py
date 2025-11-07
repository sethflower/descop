"""TrackingApp for Windows."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, date, time as dtime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    import requests
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise SystemExit(
        "The 'requests' package is required. Install it with 'pip install requests'."
    ) from exc

API_BASE = "https://tracking-api-b4jb.onrender.com"
STATE_PATH = Path(__file__).with_name("tracking_app_state.json")
QUEUE_PATH = Path(__file__).with_name("offline_queue.json")


@dataclass
class AppState:
    token: Optional[str] = None
    access_level: int = 2
    last_password: str = ""
    user_name: str = "operator"

    @classmethod
    def load(cls) -> "AppState":
        if STATE_PATH.exists():
            try:
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                return cls(**data)
            except Exception:
                STATE_PATH.unlink(missing_ok=True)
        return cls()

    def save(self) -> None:
        STATE_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


class OfflineQueue:
    _lock = threading.Lock()

    @staticmethod
    def _load() -> List[Dict[str, Any]]:
        if QUEUE_PATH.exists():
            try:
                return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
            except Exception:
                QUEUE_PATH.unlink(missing_ok=True)
        return []

    @classmethod
    def add_record(cls, record: Dict[str, Any]) -> None:
        with cls._lock:
            pending = cls._load()
            pending.append(record)
            QUEUE_PATH.write_text(json.dumps(pending, indent=2), encoding="utf-8")

    @classmethod
    def sync_pending(
        cls, token: str, callback: Optional[Callable[[int], None]] = None
    ) -> None:
        def worker() -> None:
            with cls._lock:
                pending = cls._load()
            if not pending or not token:
                return
            synced: List[Dict[str, Any]] = []
            for record in pending:
                try:
                    response = requests.post(
                        f"{API_BASE}/add_record",
                        json=record,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        timeout=10,
                    )
                    if response.status_code == 200:
                        synced.append(record)
                except requests.RequestException:
                    break
            if synced:
                with cls._lock:
                    remaining = [r for r in cls._load() if r not in synced]
                    QUEUE_PATH.write_text(
                        json.dumps(remaining, indent=2), encoding="utf-8"
                    )
            if callback:
                callback(len(synced))

        threading.Thread(target=worker, daemon=True).start()


def get_role_info(access_level: int, password: str) -> Dict[str, Any]:
    if access_level == 1 or password == "301993":
        return {"label": "🔑 Адмін", "color": "#e53935", "can_clear_history": True, "can_clear_errors": True}
    if password == "123123123":
        return {"label": "🧰 Очищення помилок", "color": "#fb8c00", "can_clear_history": False, "can_clear_errors": True}
    if access_level == 0:
        return {"label": "🧰 Оператор", "color": "#1e88e5", "can_clear_history": False, "can_clear_errors": False}
    return {"label": "👁 Перегляд", "color": "#757575", "can_clear_history": False, "can_clear_errors": False}


class TrackingApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TrackingApp Windows Edition")
        self.geometry("1280x800")
        self.minsize(1100, 720)
        self.configure(bg="#0d47a1")

        self.state_data = AppState.load()
        self._current_frame: Optional[tk.Frame] = None

        self.style = ttk.Style(self)
        self.style.configure("TButton", font=("Segoe UI", 14), padding=10)
        self.style.configure("TLabel", font=("Segoe UI", 14))
        self.style.configure("TEntry", font=("Segoe UI", 16))

        if self.state_data.token and self.state_data.user_name:
            self.show_scanner()
        elif self.state_data.token:
            self.show_username()
        else:
            self.show_login()

    def switch_to(self, frame_cls: type[tk.Frame]) -> None:
        if self._current_frame is not None:
            self._current_frame.destroy()
        frame = frame_cls(self)
        frame.pack(fill="both", expand=True)
        self._current_frame = frame

    def show_login(self) -> None:
        self.switch_to(LoginFrame)

    def show_username(self) -> None:
        self.switch_to(UserNameFrame)

    def show_scanner(self) -> None:
        self.switch_to(ScannerFrame)


class LoginFrame(tk.Frame):
    def __init__(self, app: TrackingApp) -> None:
        super().__init__(app, bg="#0d47a1")
        self.app = app
        self.password_var = tk.StringVar()
        self.error_var = tk.StringVar()
        self.loading = False

        container = tk.Frame(self, bg="#0d47a1")
        container.place(relx=0.5, rely=0.5, anchor="center")

        logo = tk.Label(container, text="TrackingApp", font=("Segoe UI", 42, "bold"), fg="white", bg="#0d47a1")
        logo.pack(pady=20)

        prompt = tk.Label(
            container,
            text="Вітаю! Введіть пароль",
            font=("Segoe UI", 24, "bold"),
            fg="white",
            bg="#0d47a1",
        )
        prompt.pack(pady=(0, 30))

        entry = ttk.Entry(container, textvariable=self.password_var, show="*")
        entry.pack(ipadx=20, ipady=12)
        entry.bind("<Return>", lambda _: self.login())

        self.error_label = tk.Label(
            container,
            textvariable=self.error_var,
            font=("Segoe UI", 14),
            fg="#ff5252",
            bg="#0d47a1",
        )
        self.error_label.pack(pady=20)

        self.button = ttk.Button(container, text="Увійти", command=self.login)
        self.button.pack(fill="x", pady=(10, 40))

        footer = tk.Label(
            container,
            text="by Dimon VR",
            font=("Segoe UI", 16, "italic"),
            fg="white",
            bg="#0d47a1",
        )
        footer.pack()

        entry.focus_set()

    def set_loading(self, value: bool) -> None:
        self.loading = value
        if value:
            self.button.configure(text="Зачекайте...", state="disabled")
        else:
            self.button.configure(text="Увійти", state="normal")

    def login(self) -> None:
        if self.loading:
            return
        password = self.password_var.get().strip()
        if not password:
            self.error_var.set("Введіть пароль")
            return

        def worker() -> None:
            try:
                response = requests.post(
                    f"{API_BASE}/login",
                    params={"password": password},
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                if response.status_code == 200:
                    data = response.json()
                    self.app.state_data.token = data.get("token")
                    self.app.state_data.access_level = data.get("access_level", 2)
                    self.app.state_data.last_password = password
                    self.app.state_data.save()
                    self.after(0, self.app.show_username)
                else:
                    try:
                        message = response.json().get("message", "Невірний пароль")
                    except Exception:
                        message = "Невірний пароль"
                    self.after(0, lambda: self.error_var.set(message))
            except requests.RequestException:
                self.after(0, lambda: self.error_var.set("Помилка підключення до сервера"))
            finally:
                self.after(0, lambda: self.set_loading(False))

        self.error_var.set("")
        self.set_loading(True)
        threading.Thread(target=worker, daemon=True).start()


class UserNameFrame(tk.Frame):
    def __init__(self, app: TrackingApp) -> None:
        super().__init__(app, bg="#f5f5f5")
        self.app = app
        self.name_var = tk.StringVar(value=app.state_data.user_name)

        wrapper = tk.Frame(self, bg="#f5f5f5")
        wrapper.place(relx=0.5, rely=0.5, anchor="center")

        label = tk.Label(
            wrapper,
            text="Введіть ім’я користувача",
            font=("Segoe UI", 28, "bold"),
            bg="#f5f5f5",
        )
        label.pack(pady=(0, 30))

        entry = ttk.Entry(wrapper, textvariable=self.name_var, justify="center")
        entry.pack(ipadx=20, ipady=12)
        entry.bind("<Return>", lambda _: self.save())

        ttk.Button(wrapper, text="Продовжити", command=self.save).pack(
            fill="x", pady=30
        )

        entry.focus_set()

    def save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Увага", "Введіть ім’я користувача")
            return
        self.app.state_data.user_name = name
        self.app.state_data.save()
        self.app.show_scanner()


class ScannerFrame(tk.Frame):
    def __init__(self, app: TrackingApp) -> None:
        super().__init__(app, bg="#f7f8fa")
        self.app = app
        self.box_var = tk.StringVar()
        self.ttn_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.online_var = tk.StringVar(value="🔄 Перевірка з’єднання...")
        self.online_color = "#fdd835"

        self.role_info = get_role_info(
            app.state_data.access_level, app.state_data.last_password
        )

        top_bar = tk.Frame(self, bg=self.online_color)
        top_bar.pack(fill="x")
        self.online_label = tk.Label(
            top_bar,
            textvariable=self.online_var,
            font=("Segoe UI", 16, "bold"),
            fg="black",
            bg=self.online_color,
            pady=8,
        )
        self.online_label.pack(fill="x")

        info_panel = tk.Frame(self, bg="white", bd=1, relief="solid")
        info_panel.pack(fill="x", padx=24, pady=(16, 0))

        left = tk.Frame(info_panel, bg="white")
        left.pack(side="left", padx=16, pady=12)
        tk.Label(
            left,
            text=f"Оператор: {app.state_data.user_name}",
            font=("Segoe UI", 20, "bold"),
            bg="white",
        ).pack(anchor="w")
        tk.Label(
            left,
            text=self.role_info["label"],
            font=("Segoe UI", 18),
            fg=self.role_info["color"],
            bg="white",
        ).pack(anchor="w", pady=(4, 0))

        right = tk.Frame(info_panel, bg="white")
        right.pack(side="right", padx=16, pady=12)
        self.step_var = tk.StringVar(value="BoxID")
        tk.Label(
            right,
            textvariable=self.step_var,
            font=("Segoe UI", 24, "bold"),
            fg="#1e88e5",
            bg="white",
        ).pack()

        buttons = tk.Frame(self, bg="#f7f8fa")
        buttons.pack(anchor="e", padx=24, pady=16)
        ttk.Button(buttons, text="Історія", command=self.open_history).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Помилки", command=self.open_errors).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="Вийти", command=self.logout).pack(
            side="left", padx=6
        )

        main = tk.Frame(self, bg="#f7f8fa")
        main.pack(expand=True)
        form = tk.Frame(main, bg="white", bd=2, relief="groove")
        form.pack(padx=80, pady=40, fill="both", expand=True)

        tk.Label(
            form,
            textvariable=self.step_var,
            font=("Segoe UI", 30, "bold"),
            bg="white",
        ).pack(pady=(40, 20))

        self.entry = ttk.Entry(form, textvariable=self.box_var, justify="center")
        self.entry.pack(padx=120, ipadx=40, ipady=18)
        self.entry.bind("<Return>", lambda _: self.to_next())

        self.ttn_entry = ttk.Entry(form, textvariable=self.ttn_var, justify="center")
        self.ttn_entry.pack(padx=120, ipadx=40, ipady=18, pady=(30, 0))
        self.ttn_entry.bind("<Return>", lambda _: self.submit())

        self.ttn_entry.pack_forget()  # start hidden until second step

        status_frame = tk.Frame(form, bg="white")
        status_frame.pack(pady=40)
        tk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 18),
            fg="#424242",
            bg="white",
            wraplength=700,
            justify="center",
        ).pack()

        self.entry.focus_set()
        self.check_connectivity()
        OfflineQueue.sync_pending(self.app.state_data.token or "")

    def set_online_state(self, online: bool) -> None:
        if online:
            self.online_color = "#43a047"
            self.online_var.set("🟢 Підключення активне")
        else:
            self.online_color = "#e53935"
            self.online_var.set("🔴 Немає зв’язку з сервером")
        self.online_label.configure(bg=self.online_color)
        self.online_label.master.configure(bg=self.online_color)

    def check_connectivity(self) -> None:
        def worker() -> None:
            try:
                response = requests.head(API_BASE, timeout=5)
                online = response.status_code < 500
            except requests.RequestException:
                online = False
            self.after(0, lambda: self.set_online_state(online))
            self.after(15000, self.check_connectivity)

        threading.Thread(target=worker, daemon=True).start()

    def to_next(self) -> None:
        value = self.box_var.get().strip()
        if not value:
            messagebox.showwarning("Увага", "Введіть BoxID")
            return
        self.step_var.set("ТТН")
        self.entry.pack_forget()
        self.ttn_entry.pack(padx=120, ipadx=40, ipady=18)
        self.ttn_entry.focus_set()

    def reset_fields(self) -> None:
        self.box_var.set("")
        self.ttn_var.set("")
        self.step_var.set("BoxID")
        self.ttn_entry.pack_forget()
        self.entry.pack(padx=120, ipadx=40, ipady=18)
        self.entry.focus_set()

    def submit(self) -> None:
        boxid = self.box_var.get().strip()
        ttn = self.ttn_var.get().strip()
        if not boxid or not ttn:
            messagebox.showwarning("Увага", "Введіть BoxID та ТТН")
            return
        record = {
            "user_name": self.app.state_data.user_name,
            "boxid": boxid,
            "ttn": ttn,
        }
        self.status_var.set("Відправлення...")

        def worker() -> None:
            token = self.app.state_data.token or ""
            if not token:
                OfflineQueue.add_record(record)
                self.after(
                    0,
                    lambda: self.status_var.set(
                        "📦 Збережено локально. Увійдіть знову, щоб синхронізувати."
                    ),
                )
                self.after(0, self.reset_fields)
                return
            try:
                response = requests.post(
                    f"{API_BASE}/add_record",
                    json=record,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=10,
                )
                if response.status_code == 200:
                    note = response.json().get("note", "")
                    if note:
                        message = f"⚠️ Дублікат: {note}"
                    else:
                        message = "✅ Успішно додано"
                    self.after(0, lambda: self.status_var.set(message))
                    self.after(0, lambda: self.set_online_state(True))
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException:
                OfflineQueue.add_record(record)
                self.after(0, lambda: self.status_var.set("📦 Збережено локально (офлайн)"))
                self.after(0, lambda: self.set_online_state(False))
            finally:
                self.after(0, self.reset_fields)
                OfflineQueue.sync_pending(token)

        threading.Thread(target=worker, daemon=True).start()

    def logout(self) -> None:
        if not messagebox.askyesno("Підтвердження", "Вийти з акаунту?"):
            return
        self.app.state_data = AppState()
        self.app.state_data.save()
        self.app.show_login()

    def open_history(self) -> None:
        HistoryWindow(self.app)

    def open_errors(self) -> None:
        ErrorsWindow(self.app, self.role_info)


class HistoryWindow(tk.Toplevel):
    def __init__(self, app: TrackingApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("Історія сканувань")
        self.geometry("1000x700")

        self.records: List[Dict[str, Any]] = []
        self.filtered: List[Dict[str, Any]] = []

        filters = tk.Frame(self)
        filters.pack(fill="x", padx=12, pady=8)

        self.box_filter = tk.StringVar()
        self.ttn_filter = tk.StringVar()
        self.user_filter = tk.StringVar()
        self.date_filter: Optional[date] = None
        self.start_time: Optional[dtime] = None
        self.end_time: Optional[dtime] = None

        self._add_filter_entry(filters, "BoxID", self.box_filter)
        self._add_filter_entry(filters, "TTN", self.ttn_filter)
        self._add_filter_entry(filters, "Користувач", self.user_filter)

        ttk.Button(filters, text="Дата", command=self.pick_date).pack(side="left", padx=4)
        ttk.Button(filters, text="Початок", command=lambda: self.pick_time(True)).pack(side="left", padx=4)
        ttk.Button(filters, text="Кінець", command=lambda: self.pick_time(False)).pack(side="left", padx=4)
        ttk.Button(filters, text="Скинути", command=self.clear_filters).pack(side="left", padx=4)
        ttk.Button(filters, text="Оновити", command=self.fetch_history).pack(side="left", padx=4)
        if get_role_info(app.state_data.access_level, app.state_data.last_password)["can_clear_history"]:
            ttk.Button(filters, text="Очистити", command=self.clear_history).pack(side="right", padx=4)

        columns = ("datetime", "boxid", "ttn", "user", "note")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        headings = {
            "datetime": "Дата",
            "boxid": "BoxID",
            "ttn": "TTN",
            "user": "Користувач",
            "note": "Примітка",
        }
        for col, text in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=180 if col == "datetime" else 140, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.fetch_history()

    def _add_filter_entry(self, parent: tk.Widget, label: str, variable: tk.StringVar) -> None:
        frame = tk.Frame(parent)
        frame.pack(side="left", padx=4)
        tk.Label(frame, text=label).pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=variable, width=16)
        entry.pack()
        entry.bind("<KeyRelease>", lambda _: self.apply_filters())

    def pick_date(self) -> None:
        value = simpledialog.askstring("Дата", "Введіть дату у форматі ДД.ММ.РРРР", parent=self)
        if value:
            try:
                self.date_filter = datetime.strptime(value, "%d.%m.%Y").date()
            except ValueError:
                messagebox.showerror("Помилка", "Невірний формат дати")
                return
        else:
            self.date_filter = None
        self.apply_filters()

    def pick_time(self, is_start: bool) -> None:
        value = simpledialog.askstring("Час", "Введіть час у форматі ГГ:ХХ", parent=self)
        if value:
            try:
                parsed = datetime.strptime(value, "%H:%M").time()
            except ValueError:
                messagebox.showerror("Помилка", "Невірний формат часу")
                return
            if is_start:
                self.start_time = parsed
            else:
                self.end_time = parsed
        else:
            if is_start:
                self.start_time = None
            else:
                self.end_time = None
        self.apply_filters()

    def clear_filters(self) -> None:
        self.box_filter.set("")
        self.ttn_filter.set("")
        self.user_filter.set("")
        self.date_filter = None
        self.start_time = None
        self.end_time = None
        self.apply_filters()

    def fetch_history(self) -> None:
        token = self.app.state_data.token
        if not token:
            messagebox.showerror("Помилка", "Необхідна авторизація")
            return

        def worker() -> None:
            try:
                response = requests.get(
                    f"{API_BASE}/get_history",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    data = response.json()
                    fallback = datetime.min.replace(tzinfo=timezone.utc)
                    data.sort(
                        key=lambda r: self._parse_datetime(r.get("datetime")) or fallback,
                        reverse=True,
                    )
                    self.records = data
                    self.after(0, self.apply_filters)
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException as exc:
                self.after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося завантажити історію: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def apply_filters(self) -> None:
        filtered = list(self.records)
        if self.box_filter.get():
            needle = self.box_filter.get().strip()
            filtered = [r for r in filtered if needle.lower() in str(r.get("boxid", "")).lower()]
        if self.ttn_filter.get():
            needle = self.ttn_filter.get().strip()
            filtered = [r for r in filtered if needle.lower() in str(r.get("ttn", "")).lower()]
        if self.user_filter.get():
            needle = self.user_filter.get().strip()
            filtered = [r for r in filtered if needle.lower() in str(r.get("user_name", "")).lower()]
        if self.date_filter:
            filtered = [
                r
                for r in filtered
                if self._parse_datetime(r.get("datetime"))
                and self._parse_datetime(r.get("datetime")).date() == self.date_filter
            ]
        if self.start_time or self.end_time:
            tmp = []
            for r in filtered:
                dt = self._parse_datetime(r.get("datetime"))
                if not dt:
                    continue
                tm = dt.time()
                if self.start_time and tm < self.start_time:
                    continue
                if self.end_time and tm > self.end_time:
                    continue
                tmp.append(r)
            filtered = tmp

        self.filtered = filtered
        for row in self.tree.get_children():
            self.tree.delete(row)
        for item in filtered:
            dt = self._parse_datetime(item.get("datetime"))
            dt_txt = dt.strftime("%d.%m.%Y %H:%M:%S") if dt else item.get("datetime", "")
            self.tree.insert(
                "",
                "end",
                values=(
                    dt_txt,
                    item.get("boxid", ""),
                    item.get("ttn", ""),
                    item.get("user_name", ""),
                    item.get("note", ""),
                ),
            )

    def clear_history(self) -> None:
        if not messagebox.askyesno("Підтвердження", "Очистити історію? Це незворотньо."):
            return
        token = self.app.state_data.token
        if not token:
            return

        def worker() -> None:
            try:
                response = requests.delete(
                    f"{API_BASE}/clear_tracking",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    def update() -> None:
                        self.records.clear()
                        self.apply_filters()

                    self.after(0, update)
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException as exc:
                self.after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося очистити: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone()
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None


class ErrorsWindow(tk.Toplevel):
    def __init__(self, app: TrackingApp, role_info: Dict[str, Any]) -> None:
        super().__init__(app)
        self.app = app
        self.role_info = role_info
        self.title("Журнал помилок")
        self.geometry("900x650")

        self.records: List[Dict[str, Any]] = []

        toolbar = tk.Frame(self)
        toolbar.pack(fill="x", padx=12, pady=8)
        ttk.Button(toolbar, text="Оновити", command=self.fetch_errors).pack(side="left", padx=4)
        if role_info.get("can_clear_errors"):
            ttk.Button(toolbar, text="Очистити всі", command=self.clear_errors).pack(side="left", padx=4)

        columns = ("datetime", "boxid", "ttn", "user", "reason")
        self.tree = ttk.Treeview(self, columns=columns, show="headings")
        headings = {
            "datetime": "Дата",
            "boxid": "BoxID",
            "ttn": "TTN",
            "user": "Користувач",
            "reason": "Причина",
        }
        for col, text in headings.items():
            self.tree.heading(col, text=text)
            self.tree.column(col, width=160 if col == "reason" else 140, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        if role_info.get("can_clear_errors"):
            self.tree.bind("<Double-1>", self.delete_selected_error)

        self.fetch_errors()

    def fetch_errors(self) -> None:
        token = self.app.state_data.token
        if not token:
            messagebox.showerror("Помилка", "Необхідна авторизація")
            return

        def worker() -> None:
            try:
                response = requests.get(
                    f"{API_BASE}/get_errors",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    data = response.json()
                    fallback = datetime.min.replace(tzinfo=timezone.utc)
                    data.sort(
                        key=lambda r: HistoryWindow._parse_datetime(r.get("datetime"))
                        or fallback,
                        reverse=True,
                    )
                    self.records = data
                    self.after(0, self.render_records)
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException as exc:
                self.after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося завантажити: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def render_records(self) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)
        for item in self.records:
            dt = HistoryWindow._parse_datetime(item.get("datetime"))
            dt_txt = dt.strftime("%d.%m.%Y %H:%M:%S") if dt else item.get("datetime", "")
            reason = (
                item.get("error_message")
                or item.get("reason")
                or item.get("note")
                or item.get("message")
                or item.get("error")
                or "Причина не вказана"
            )
            self.tree.insert(
                "",
                "end",
                iid=str(item.get("id", "")),
                values=(
                    dt_txt,
                    item.get("boxid", ""),
                    item.get("ttn", ""),
                    item.get("user_name", ""),
                    reason,
                ),
            )

    def clear_errors(self) -> None:
        if not messagebox.askyesno("Підтвердження", "Очистити журнал помилок?"):
            return
        token = self.app.state_data.token
        if not token:
            return

        def worker() -> None:
            try:
                response = requests.delete(
                    f"{API_BASE}/clear_errors",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    def update() -> None:
                        self.records.clear()
                        self.render_records()

                    self.after(0, update)
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException as exc:
                self.after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося очистити: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def delete_selected_error(self, event: tk.Event) -> None:
        item_id = self.tree.focus()
        if not item_id:
            return
        try:
            record_id = int(float(item_id))
        except ValueError:
            return
        if not messagebox.askyesno("Підтвердження", f"Видалити помилку #{record_id}?"):
            return
        token = self.app.state_data.token
        if not token:
            return

        def worker() -> None:
            try:
                response = requests.delete(
                    f"{API_BASE}/delete_error/{record_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    def update() -> None:
                        self.records = [
                            r for r in self.records if r.get("id") != record_id
                        ]
                        self.render_records()

                    self.after(0, update)
                else:
                    raise requests.RequestException(f"status {response.status_code}")
            except requests.RequestException as exc:
                self.after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося видалити: {exc}"))

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    app = TrackingApp()
    app.mainloop()


if __name__ == "__main__":  # pragma: no cover
    main()
