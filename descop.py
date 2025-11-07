# tracking_app_tk_api.py
# Tkinter UI "как в первой версии", но с логикой работы через REST API
# API совместим с: https://tracking-api-b4jb.onrender.com
# Вкладки: Сканування / Історія / Помилки / Довідка
# Офлайн-очередь: локальная SQLite (offline_queue.db), авто-синхронизация раз в 60 сек

import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkcalendar import DateEntry

# ------------------ Константы ------------------

BASE_URL = "https://tracking-api-b4jb.onrender.com"

APP_DIR = Path.home() / ".trackingapp_tk"
APP_DIR.mkdir(parents=True, exist_ok=True)
OFFLINE_DB_PATH = APP_DIR / "offline_queue.db"
CONFIG_PATH = APP_DIR / "config.json"
HELP_PATH = APP_DIR / "help.txt"

SYNC_INTERVAL_MS = 60_000  # 60 секунд


# ------------------ Конфигурация ------------------

def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "token": None,
        "access_level": 2,
        "last_password": "",
        "user_name": "",
    }


def save_config(cfg: Dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


CONFIG = load_config()


# ------------------ Офлайн-очередь (SQLite) ------------------

def _ensure_offline_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL
        )
    """)
    conn.commit()


def _open_offline_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(OFFLINE_DB_PATH))
    _ensure_offline_schema(conn)
    return conn


def enqueue_offline(record: Dict[str, str]) -> None:
    try:
        payload = json.dumps(record, ensure_ascii=False)
        with _open_offline_conn() as conn:
            conn.execute("INSERT INTO queue(payload) VALUES (?)", (payload,))
            conn.commit()
    except Exception:
        pass


def dequeue_all_offline() -> List[Dict[str, str]]:
    data: List[Dict[str, str]] = []
    try:
        with _open_offline_conn() as conn:
            cur = conn.execute("SELECT id, payload FROM queue ORDER BY id ASC")
            rows = cur.fetchall()
            ids = []
            for row_id, payload in rows:
                try:
                    data.append(json.loads(payload))
                except Exception:
                    pass
                ids.append(row_id)
            if ids:
                conn.executemany("DELETE FROM queue WHERE id = ?", ((i,) for i in ids))
                conn.commit()
    except Exception:
        pass
    return data


def pending_offline_count() -> int:
    try:
        with _open_offline_conn() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM queue")
            (count,) = cur.fetchone()
            return int(count)
    except Exception:
        return 0


# ------------------ API клиент ------------------

class ApiError(RuntimeError):
    pass


class ApiClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _handle(self, resp: requests.Response) -> Any:
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise ApiError(f"HTTP {resp.status_code}: {body}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text

    # POST /login?password=...
    def login(self, password: str) -> Dict[str, Any]:
        r = requests.post(
            f"{self.base_url}/login",
            params={"password": password},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        data = self._handle(r)
        if not isinstance(data, dict) or "token" not in data:
            raise ApiError("Unexpected login response")
        return data

    # POST /add_record
    def add_record(self, token: str, record: Dict[str, str]) -> Dict[str, Any]:
        r = requests.post(
            f"{self.base_url}/add_record",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=record,
            timeout=10,
        )
        data = self._handle(r)
        return data if isinstance(data, dict) else {}

    # GET /get_history
    def get_history(self, token: str) -> List[Dict[str, Any]]:
        r = requests.get(
            f"{self.base_url}/get_history",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = self._handle(r)
        if isinstance(data, list):
            return data
        raise ApiError("Unexpected history response")

    # GET /get_errors
    def get_errors(self, token: str) -> List[Dict[str, Any]]:
        r = requests.get(
            f"{self.base_url}/get_errors",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        data = self._handle(r)
        if isinstance(data, list):
            return data
        raise ApiError("Unexpected errors response")

    # DELETE /clear_errors
    def clear_errors(self, token: str) -> None:
        r = requests.delete(
            f"{self.base_url}/clear_errors",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        self._handle(r)

    # DELETE /delete_error/{id}
    def delete_error(self, token: str, error_id: int) -> None:
        r = requests.delete(
            f"{self.base_url}/delete_error/{error_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        self._handle(r)


API = ApiClient()


# ------------------ Уровни доступа ------------------
# 0 = ограниченный (оператор): Сканування, Історія, Помилки; нельзя удалять всё
# 1 = админ: всё то же + может удалять/очищать ошибки и редактировать Довідку
# 2 = только просмотр: видит Історія, Помилки, Довідка; без редактирования/сканирования

def apply_access_to_tabs(tab_control: ttk.Notebook, tabs: Dict[str, ttk.Frame], access_level: int) -> None:
    # Скрываем/отключаем вкладки
    # Сканування скрыть для уровня 2
    if access_level == 2:
        try:
            tab_control.tab(tabs["scan"], state='hidden')
        except Exception:
            pass
    else:
        try:
            tab_control.tab(tabs["scan"], state='normal')
        except Exception:
            pass


# ------------------ Вспомогательные UI-функции ------------------

def add_copy_paste_to_entry(entry: tk.Entry) -> None:
    def cut_text(e=None):
        entry.event_generate("<<Cut>>"); return "break"

    def copy_text(e=None):
        entry.event_generate("<<Copy>>"); return "break"

    def paste_text(e=None):
        entry.event_generate("<<Paste>>"); return "break"

    menu = tk.Menu(entry, tearoff=0)
    menu.add_command(label="Вырезать", command=cut_text)
    menu.add_command(label="Копировать", command=copy_text)
    menu.add_command(label="Вставить", command=paste_text)

    def show_menu(event):
        menu.tk_popup(event.x_root, event.y_root)

    entry.bind("<Button-3>", show_menu)
    entry.bind("<Control-c>", copy_text)
    entry.bind("<Control-x>", cut_text)
    entry.bind("<Control-v>", paste_text)


def add_copy_paste_to_text(text_widget: tk.Text) -> None:
    def cut_text(e=None):
        text_widget.event_generate("<<Cut>>"); return "break"

    def copy_text(e=None):
        text_widget.event_generate("<<Copy>>"); return "break"

    def paste_text(e=None):
        text_widget.event_generate("<<Paste>>"); return "break"

    menu = tk.Menu(text_widget, tearoff=0)
    menu.add_command(label="Вырезать", command=cut_text)
    menu.add_command(label="Копировать", command=copy_text)
    menu.add_command(label="Вставить", command=paste_text)

    def show_menu(event):
        menu.tk_popup(event.x_root, event.y_root)

    text_widget.bind("<Button-3>", show_menu)
    text_widget.bind("<Control-c>", copy_text)
    text_widget.bind("<Control-x>", cut_text)
    text_widget.bind("<Control-v>", paste_text)


def add_right_click_menu_treeview(tree: ttk.Treeview) -> None:
    menu = tk.Menu(tree, tearoff=0)
    menu.add_command(label="Скопировать ячейку", command=lambda: copy_selected_cell(tree))
    menu.add_command(label="Скопировать всю строку", command=lambda: copy_selected_row(tree))

    def show_menu(event):
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id:
            return
        tree.selection_set(row_id)
        tree.clicked_row = row_id
        tree.clicked_col = col_id
        menu.tk_popup(event.x_root, event.y_root)

    tree.bind("<Button-3>", show_menu)


def copy_selected_cell(tree: ttk.Treeview) -> None:
    try:
        row_id = getattr(tree, "clicked_row", None)
        col_id = getattr(tree, "clicked_col", None)
        if not row_id or not col_id:
            return
        col_index = int(col_id.replace('#', '')) - 1
        values = tree.item(row_id, "values")
        if 0 <= col_index < len(values):
            cell_value = values[col_index]
            tree.clipboard_clear()
            tree.clipboard_append(cell_value)
    except Exception:
        pass


def copy_selected_row(tree: ttk.Treeview) -> None:
    try:
        row_id = getattr(tree, "clicked_row", None)
        if not row_id:
            return
        values = tree.item(row_id, "values")
        row_str = "\t".join(str(v) for v in values)
        tree.clipboard_clear()
        tree.clipboard_append(row_str)
    except Exception:
        pass


# ------------------ Хелпер по датам ------------------

def parse_iso_to_local_str(timestamp: Optional[str]) -> str:
    if not timestamp:
        return ""
    try:
        # допускаем ISO в формате с Z
        ts = timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        # без pytz: просто вернём как есть в локальном представлении
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return timestamp


# ------------------ Главное приложение ------------------

class App:
    def __init__(self) -> None:
        self.window = tk.Tk()
        self.window.title("TrackinApp API (Tkinter)")
        try:
            self.window.state('zoomed')
        except Exception:
            self.window.geometry("1200x800")

        style = ttk.Style()
        style.configure("TNotebook.Tab", font=("Arial", 16))

        self.tab_control = ttk.Notebook(self.window)

        # Вкладки
        self.scan_tab = ttk.Frame(self.tab_control)
        self.history_tab = ttk.Frame(self.tab_control)
        self.errors_tab = ttk.Frame(self.tab_control)
        self.help_tab = ttk.Frame(self.tab_control)

        self.tab_control.add(self.scan_tab, text='Сканування')
        self.tab_control.add(self.history_tab, text='Історія')
        self.tab_control.add(self.errors_tab, text='Помилки')
        self.tab_control.add(self.help_tab, text='Довідка')
        self.tab_control.pack(expand=1, fill='both')

        # Статус синхронизации
        self.status_bar = tk.Label(self.window, text="", anchor="w")
        self.status_bar.pack(side="bottom", fill="x")

        # Доступ
        self.user_access_level = int(CONFIG.get("access_level", 2))
        self.token: Optional[str] = CONFIG.get("token")

        # Инициализация интерфейса
        self._build_scan_tab()
        self._build_history_tab()
        self._build_errors_tab()
        self._build_help_tab()

        # Применить права (скрыть вкладки при необходимости)
        apply_access_to_tabs(self.tab_control, {
            "scan": self.scan_tab
        }, self.user_access_level)

        # Запустить цикл синхронизации офлайна
        self._schedule_sync()

        # Открыть окно логина
        self._check_login()

    # ---------- ЛОГИН ----------

    def _check_login(self) -> None:
        if not self.token:
            self._show_login_dialog()
        else:
            # Попросим имя оператора (если пустое)
            if not CONFIG.get("user_name"):
                self._prompt_user_name()

    def _show_login_dialog(self) -> None:
        dlg = tk.Toplevel(self.window)
        dlg.title("Введіть пароль")
        dlg.grab_set()
        dlg.geometry("500x200")
        ttk.Label(dlg, text="Будь ласка, введіть пароль:", font=("Arial", 14)).pack(pady=20)
        pwd = tk.Entry(dlg, font=("Arial", 14), show="*", width=30)
        pwd.pack(pady=10)
        pwd.focus_set()

        status = ttk.Label(dlg, text="", foreground="red")
        status.pack(pady=5)

        def submit():
            password = pwd.get().strip()
            if not password:
                status.config(text="Пароль порожній")
                return
            try:
                data = API.login(password)
            except Exception as e:
                status.config(text=f"Помилка: {e}")
                return
            # сохранить токен и уровень доступа
            CONFIG["token"] = data.get("token")
            CONFIG["access_level"] = int(data.get("access_level", 2))
            CONFIG["last_password"] = password
            save_config(CONFIG)
            self.token = CONFIG["token"]
            self.user_access_level = CONFIG["access_level"]

            # применим доступ к вкладкам
            apply_access_to_tabs(self.tab_control, {"scan": self.scan_tab}, self.user_access_level)

            dlg.destroy()
            # спросить имя пользователя, если не задано
            if not CONFIG.get("user_name"):
                self._prompt_user_name()
            self._refresh_all()

        btn = ttk.Button(dlg, text="Увійти", command=submit)
        btn.pack(pady=10)
        dlg.bind("<Return>", lambda e: submit())

    def _prompt_user_name(self) -> None:
        dlg = tk.Toplevel(self.window)
        dlg.title("Введіть своє прізвище")
        dlg.grab_set()
        dlg.geometry("600x230")
        ttk.Label(dlg, text="Введіть своє прізвище (оператор):", font=("Arial", 16)).pack(pady=20)
        entry = tk.Entry(dlg, font=("Arial", 16), width=30)
        entry.pack(pady=10)
        entry.focus_set()
        add_copy_paste_to_entry(entry)

        def submit():
            name = entry.get().strip()
            if not name:
                messagebox.showerror("Помилка", "Будь-ласка введіть прізвище.")
                return
            CONFIG["user_name"] = name
            save_config(CONFIG)
            dlg.destroy()

        ttk.Button(dlg, text="Зберегти", command=submit).pack(pady=10)
        dlg.bind("<Return>", lambda e: submit())

    # ---------- СКАНУВАННЯ ----------

    def _build_scan_tab(self) -> None:
        self.scan_label = tk.Label(self.scan_tab, text="Введіть своє прізвище", font=("Arial", 48))
        self.scan_label.pack(pady=20)

        self.scan_entry = tk.Entry(self.scan_tab, font=("Arial", 36), width=25)
        self.scan_entry.pack(pady=10)
        self.scan_entry.focus_set()
        add_copy_paste_to_entry(self.scan_entry)

        # статус
        self.scan_status = tk.Label(self.scan_tab, text="", font=("Arial", 18), fg="#2b72ff")
        self.scan_status.pack(pady=10)

        # счетчик офлайна
        self.offline_label = tk.Label(self.scan_tab, text="Офлайн записів: 0", font=("Arial", 14))
        self.offline_label.pack(pady=5)

        # Привязки
        self.scan_entry.bind('<Return>', self._on_enter_name)

    def _on_enter_name(self, event=None):
        user_name = self.scan_entry.get().strip()
        if not user_name:
            messagebox.showerror("Помилка", "Будь-ласка введіть своє прізвище.")
            return
        CONFIG["user_name"] = user_name
        save_config(CONFIG)
        self.scan_entry.delete(0, tk.END)
        self.scan_label.config(text="Відскануйте BoxID:")
        self.scan_entry.bind('<Return>', lambda evt: self._on_enter_boxid(evt, user_name))

    def _on_enter_boxid(self, event, user_name: str):
        self._boxid = self.scan_entry.get().strip()
        if not self._boxid:
            messagebox.showerror("Помилка", "BoxID не може бути порожнім")
            return
        self.scan_entry.delete(0, tk.END)
        self.scan_label.config(text="Відскануйте ТТН:")
        self.scan_entry.bind('<Return>', lambda evt: self._on_enter_ttn(evt, user_name, self._boxid))

    def _on_enter_ttn(self, event, user_name: str, boxid: str):
        ttn = self.scan_entry.get().strip()
        if not ttn:
            messagebox.showerror("Помилка", "ТТН не може бути порожнім.")
            return

        record = {"user_name": user_name, "boxid": boxid, "ttn": ttn}

        if not self.token:
            messagebox.showwarning("Сесія", "Потрібно увійти знову")
            self._show_login_dialog()
            return

        try:
            resp = API.add_record(self.token, record)
            note = resp.get("note") if isinstance(resp, dict) else None
            if note:
                self.scan_status.config(text=f"Дублікат: {note}", fg="#ff4d4d")
            else:
                self.scan_status.config(text="Успішно додано", fg="#2b9e43")
            # после успешного добавления — попытка синка офлайна
            self._sync_offline()
        except Exception:
            # офлайн — запишем в очередь
            enqueue_offline(record)
            self.scan_status.config(text="Офлайн: запис збережено локально", fg="#ff9f2d")

        self.scan_entry.delete(0, tk.END)
        self.scan_label.config(text="Відскануйте BoxID:")
        self.scan_entry.bind('<Return>', lambda evt: self._on_enter_boxid(evt, user_name))
        self._refresh_offline_count()
        # обновим историю (если откроют вкладку)
        self._reload_history()

    def _refresh_offline_count(self) -> None:
        self.offline_label.config(text=f"Офлайн записів: {pending_offline_count()}")

    # ---------- ІСТОРІЯ ----------

    def _build_history_tab(self) -> None:
        history_label = tk.Label(self.history_tab, text="Історія сканування", font=("Arial", 36))
        history_label.pack(pady=10)

        filter_frame = ttk.Frame(self.history_tab)
        filter_frame.pack(pady=10, fill='x')

        tk.Label(filter_frame, text="Хто відсканував", font=("Arial", 14)).grid(row=0, column=0, padx=5, sticky="w")
        self.filter_user_entry = tk.Entry(filter_frame, font=("Arial", 14), width=30)
        self.filter_user_entry.grid(row=0, column=1, padx=5)
        add_copy_paste_to_entry(self.filter_user_entry)
        self.filter_user_entry.bind("<KeyRelease>", lambda e: self._reload_history())

        tk.Label(filter_frame, text="Дата:", font=("Arial", 14)).grid(row=0, column=3, padx=5, sticky="w")
        self.filter_date_entry = DateEntry(
            filter_frame, font=("Arial", 14), width=20, date_pattern='y-mm-dd',
            showerror=False
        )
        self.filter_date_entry.grid(row=0, column=4, padx=5)
        add_copy_paste_to_entry(self.filter_date_entry)
        # по умолчанию поле очищаем — как в первой версии
        self.filter_date_entry.delete(0, "end")
        self.filter_date_entry.bind("<<DateEntrySelected>>", lambda e: self._reload_history())
        self.filter_date_entry.bind("<KeyRelease>", lambda e: self._reload_history())

        tk.Label(filter_frame, text="Номер BoxID:", font=("Arial", 14)).grid(row=1, column=0, padx=5, sticky="w")
        self.filter_boxid_entry = tk.Entry(filter_frame, font=("Arial", 14), width=30)
        self.filter_boxid_entry.grid(row=1, column=1, padx=5)
        add_copy_paste_to_entry(self.filter_boxid_entry)
        self.filter_boxid_entry.bind("<KeyRelease>", lambda e: self._reload_history())

        tk.Label(filter_frame, text="Номер ТТН:", font=("Arial", 14)).grid(row=1, column=3, padx=5, sticky="w")
        self.filter_ttn_entry = tk.Entry(filter_frame, font=("Arial", 14), width=30)
        self.filter_ttn_entry.grid(row=1, column=4, padx=5)
        add_copy_paste_to_entry(self.filter_ttn_entry)
        self.filter_ttn_entry.bind("<KeyRelease>", lambda e: self._reload_history())

        self.show_duplicates_var = tk.BooleanVar()
        dup_chk = tk.Checkbutton(
            filter_frame, text="Показати тільки дублікати", font=("Arial", 14),
            variable=self.show_duplicates_var, command=self._reload_history
        )
        dup_chk.grid(row=2, column=0, columnspan=2, pady=10, sticky='w')

        # Таблица
        tree_frame = tk.Frame(self.history_tab)
        tree_frame.pack(fill='both', expand=True)

        scrollbar = ttk.Scrollbar(tree_frame)
        scrollbar.pack(side="right", fill="y")

        self.history_tree = ttk.Treeview(
            tree_frame,
            columns=("User", "BoxID", "TTN", "DateTime", "Note"),
            show='headings',
            yscrollcommand=scrollbar.set
        )
        self.history_tree.heading("User", text="Хто вніс данні")
        self.history_tree.heading("BoxID", text="Номер BoxID")
        self.history_tree.heading("TTN", text="Номер ТТН НП")
        self.history_tree.heading("DateTime", text="Дата та час внесення даних")
        self.history_tree.heading("Note", text="Примітка")
        self.history_tree.pack(fill='both', expand=True)

        scrollbar.config(command=self.history_tree.yview)
        add_right_click_menu_treeview(self.history_tree)

        self.history_tree.tag_configure("duplicate", background="yellow")

        # внутренний кэш истории
        self._history_cache: List[Dict[str, Any]] = []

        # кнопки "дубликат ОК" как в первой версии — удалены (нет note в API для редактирования)
        # отрисовка будет подсвечивать дубли на лету (клиентская эвристика)

    def _reload_history(self) -> None:
        # фильтрация локально по кэшу
        user_filter = self.filter_user_entry.get().strip().lower()
        date_filter = self.filter_date_entry.get().strip()
        boxid_filter = self.filter_boxid_entry.get().strip().lower()
        ttn_filter = self.filter_ttn_entry.get().strip().lower()
        show_duplicates = self.show_duplicates_var.get()

        for i in self.history_tree.get_children():
            self.history_tree.delete(i)

        rows = []
        for rec in self._history_cache:
            uname = str(rec.get("user_name", "")).lower()
            boxid = str(rec.get("boxid", "")).lower()
            ttn = str(rec.get("ttn", "")).lower()
            dt = parse_iso_to_local_str(rec.get("datetime"))
            note = str(rec.get("note", ""))

            if user_filter and not uname.startswith(user_filter):
                continue
            if date_filter and not dt.startswith(date_filter):
                continue
            if boxid_filter and not boxid.startswith(boxid_filter):
                continue
            if ttn_filter and not ttn.startswith(ttn_filter):
                continue
            rows.append((rec.get("user_name", ""), rec.get("boxid", ""), rec.get("ttn", ""), dt, note))

        # клиентская подсветка дублей, если note не пришло
        # критерии дубля: повторяющийся (boxid, ttn) или повтор boxid либо ttn
        pair_count: Dict[str, int] = {}
        box_count: Dict[str, int] = {}
        ttn_count: Dict[str, int] = {}

        for _, b, t, _, _ in rows:
            pair_key = f"{b}|{t}"
            pair_count[pair_key] = pair_count.get(pair_key, 0) + 1
            box_count[b] = box_count.get(b, 0) + 1
            ttn_count[t] = ttn_count.get(t, 0) + 1

        for row in rows:
            uname, b, t, dt, note = row
            is_dup = False
            if note:
                is_dup = True
            else:
                if pair_count.get(f"{b}|{t}", 0) > 1 or box_count.get(b, 0) > 1 or ttn_count.get(t, 0) > 1:
                    is_dup = True
                    if not note:
                        note = "Можливий дублікат (клієнтська перевірка)"
            values = (uname, b, t, dt, note)

            if show_duplicates:
                if is_dup:
                    self.history_tree.insert("", tk.END, values=values, tags=("duplicate",))
            else:
                if is_dup:
                    self.history_tree.insert("", tk.END, values=values, tags=("duplicate",))
                else:
                    self.history_tree.insert("", tk.END, values=values)

    def _load_history_from_api(self) -> None:
        if not self.token:
            return
        try:
            data = API.get_history(self.token)
            # сортировка по дате убыванию
            data.sort(key=lambda d: d.get("datetime") or "", reverse=True)
            self._history_cache = data
            self._reload_history()
        except Exception as e:
            # тихо показывать ошибку разово можно в статусе
            self._set_status(f"Помилка завантаження історії: {e}")

    # ---------- ПОМИЛКИ ----------

    def _build_errors_tab(self) -> None:
        errors_label = tk.Label(self.errors_tab, text="Помилки сканування", font=("Arial", 36))
        errors_label.pack(pady=10)

        filter_frame = ttk.Frame(self.errors_tab)
        filter_frame.pack(pady=10, fill='x')

        tk.Label(filter_frame, text="Номер BoxID:", font=("Arial", 14)).grid(row=0, column=0, padx=5, sticky='w')
        self.filter_boxid_entry_errors = tk.Entry(filter_frame, font=("Arial", 14), width=30)
        self.filter_boxid_entry_errors.grid(row=0, column=1, padx=5)
        add_copy_paste_to_entry(self.filter_boxid_entry_errors)
        self.filter_boxid_entry_errors.bind("<KeyRelease>", lambda e: self._apply_errors_filter())

        tk.Label(filter_frame, text="Номер ТТН:", font=("Arial", 14)).grid(row=0, column=3, padx=5, sticky='w')
        self.filter_ttn_entry_errors = tk.Entry(filter_frame, font=("Arial", 14), width=30)
        self.filter_ttn_entry_errors.grid(row=0, column=4, padx=5)
        add_copy_paste_to_entry(self.filter_ttn_entry_errors)
        self.filter_ttn_entry_errors.bind("<KeyRelease>", lambda e: self._apply_errors_filter())

        tree_frame = tk.Frame(self.errors_tab)
        tree_frame.pack(fill='both', expand=True)

        scrollbar_errors = ttk.Scrollbar(tree_frame)
        scrollbar_errors.pack(side="right", fill="y")

        self.errors_tree = ttk.Treeview(
            tree_frame,
            columns=("ID", "User", "BoxID", "TTN", "DateTime", "Message"),
            show='headings',
            yscrollcommand=scrollbar_errors.set
        )
        self.errors_tree.heading("ID", text="ID")
        self.errors_tree.heading("User", text="Прізвище")
        self.errors_tree.heading("BoxID", text="BoxID")
        self.errors_tree.heading("TTN", text="ТТН")
        self.errors_tree.heading("DateTime", text="Дата та час")
        self.errors_tree.heading("Message", text="Повідомлення")
        self.errors_tree.pack(fill='both', expand=True)

        add_right_click_menu_treeview(self.errors_tree)
        scrollbar_errors.config(command=self.errors_tree.yview)

        # Кнопки действий, в зависимости от прав
        btns = ttk.Frame(self.errors_tab)
        btns.pack(pady=10)

        self.btn_reload_errors = ttk.Button(btns, text="Завантажити помилки", command=self._load_errors_from_api)
        self.btn_reload_errors.grid(row=0, column=0, padx=5)

        self.btn_delete_selected_error = ttk.Button(
            btns, text="Видалити виділену помилку", command=self._delete_selected_error
        )
        self.btn_delete_selected_error.grid(row=0, column=1, padx=5)

        self.btn_clear_errors = ttk.Button(btns, text="Видалити всі помилки", command=self._clear_all_errors)
        self.btn_clear_errors.grid(row=0, column=2, padx=5)

        # ограничения доступов
        self._apply_error_tab_permissions()

        # кэш ошибок
        self._errors_cache: List[Dict[str, Any]] = []

    def _apply_error_tab_permissions(self) -> None:
        # Только админ (1) может удалять/очищать ошибки
        can_clear = self.user_access_level == 1
        self.btn_delete_selected_error.config(state=("normal" if can_clear else "disabled"))
        self.btn_clear_errors.config(state=("normal" if can_clear else "disabled"))

    def _load_errors_from_api(self) -> None:
        if not self.token:
            return
        try:
            data = API.get_errors(self.token)
            # ожидаем: [{id, user_name?, boxid, ttn, datetime, error_message?}]
            data.sort(key=lambda d: d.get("datetime") or "", reverse=True)
            self._errors_cache = data
            self._apply_errors_filter()
        except Exception as e:
            self._set_status(f"Помилка завантаження помилок: {e}")

    def _apply_errors_filter(self) -> None:
        box_filter = self.filter_boxid_entry_errors.get().strip().lower()
        ttn_filter = self.filter_ttn_entry_errors.get().strip().lower()

        for i in self.errors_tree.get_children():
            self.errors_tree.delete(i)

        for rec in self._errors_cache:
            rec_box = str(rec.get("boxid", "")).lower()
            rec_ttn = str(rec.get("ttn", "")).lower()
            if box_filter and not rec_box.startswith(box_filter):
                continue
            if ttn_filter and not rec_ttn.startswith(ttn_filter):
                continue

            self.errors_tree.insert("", tk.END, values=(
                rec.get("id", ""),
                rec.get("user_name", ""),
                rec.get("boxid", ""),
                rec.get("ttn", ""),
                parse_iso_to_local_str(rec.get("datetime")),
                rec.get("error_message", ""),
            ))

    def _delete_selected_error(self) -> None:
        if self.user_access_level != 1:
            return
        if not self.token:
            return
        sel = self.errors_tree.selection()
        if not sel:
            return
        item = self.errors_tree.item(sel[0])
        values = item.get("values", [])
        if not values:
            return
        try:
            error_id = int(values[0])
        except Exception:
            return
        if messagebox.askyesno("Видалення", f"Видалити помилку #{error_id}?"):
            try:
                API.delete_error(self.token, error_id)
                self._load_errors_from_api()
            except Exception as e:
                messagebox.showerror("Помилка", str(e))

    def _clear_all_errors(self) -> None:
        if self.user_access_level != 1:
            return
        if not self.token:
            return
        if messagebox.askyesno("Очищення помилок", "Видалити всі помилки?"):
            try:
                API.clear_errors(self.token)
                self._load_errors_from_api()
            except Exception as e:
                messagebox.showerror("Помилка", str(e))

    # ---------- ДОВІДКА ----------

    def _build_help_tab(self) -> None:
        help_text_label = tk.Label(self.help_tab, text="Інструкція по використанню програми", font=("Arial", 24))
        help_text_label.pack(pady=10)

        self.help_textbox = tk.Text(self.help_tab, wrap='word', font=("Arial", 14), height=20, width=80)
        self.help_textbox.pack(pady=10, padx=10, fill='both', expand=True)
        add_copy_paste_to_text(self.help_textbox)

        # загрузить локальный help
        self.help_textbox.insert(tk.END, self._load_help_text())
        self.help_textbox.config(state='disabled')

        if self.user_access_level == 1:
            edit_button = tk.Button(self.help_tab, text="Добавить/Изменить инструкцію",
                                    font=("Arial", 14), command=self._edit_help_text)
            edit_button.pack(pady=5)

    def _load_help_text(self) -> str:
        if HELP_PATH.exists():
            try:
                return HELP_PATH.read_text(encoding="utf-8")
            except Exception:
                pass
        return "Поки що інструкція відсутня. Натисніть «Добавить/Изменить инструкцію», щоб додати."

    def _save_help_text(self, text: str) -> None:
        try:
            HELP_PATH.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def _edit_help_text(self) -> None:
        if self.user_access_level != 1:
            return
        win = tk.Toplevel(self.window)
        win.title("Редагувати інструкцію")
        win.geometry("800x600")

        txt = tk.Text(win, wrap='word', font=("Arial", 14))
        txt.pack(padx=10, pady=10, fill='both', expand=True)
        add_copy_paste_to_text(txt)
        txt.insert(tk.END, self._load_help_text())

        def save_and_close():
            new_text = txt.get("1.0", tk.END).strip()
            self._save_help_text(new_text)
            self.help_textbox.config(state='normal')
            self.help_textbox.delete("1.0", tk.END)
            self.help_textbox.insert(tk.END, new_text)
            self.help_textbox.config(state='disabled')
            win.destroy()

        tk.Button(win, text="Зберегти", command=save_and_close, font=("Arial", 14)).pack(pady=10)

    # ---------- СИНХРОНИЗАЦИЯ ОФФЛАЙН ----------

    def _sync_offline(self) -> None:
        if not self.token:
            self._refresh_offline_count()
            return
        pending = dequeue_all_offline()
        if not pending:
            self._refresh_offline_count()
            return
        failed: List[Dict[str, str]] = []
        for rec in pending:
            try:
                API.add_record(self.token, rec)
            except Exception:
                failed.append(rec)
        for rec in failed:
            enqueue_offline(rec)
        self._refresh_offline_count()
        if failed:
            self._set_status("Статус: офлайн (є не передані записи)")
        else:
            self._set_status("Статус: онлайн (офлайн-чергу синхронізовано)")
            # при успешном синке — обновим историю
            self._load_history_from_api()

    def _schedule_sync(self) -> None:
        self.window.after(SYNC_INTERVAL_MS, self._scheduled_sync_tick)

    def _scheduled_sync_tick(self) -> None:
        try:
            self._sync_offline()
        finally:
            self._schedule_sync()

    # ---------- Обновления ----------

    def _refresh_all(self) -> None:
        self._refresh_offline_count()
        self._load_history_from_api()
        self._load_errors_from_api()

    # ---------- Статус ----------

    def _set_status(self, text: str) -> None:
        self.status_bar.config(text=text)

    # ---------- Mainloop ----------

    def run(self) -> None:
        self.window.mainloop()


# ------------------ Запуск ------------------

if __name__ == "__main__":
    App().run()
