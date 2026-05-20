APP_VERSION = "1.0.4"

import os
import sys
import json
import requests
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from packaging.version import Version
from PIL import Image, ImageTk

# =========================
# CONFIG
# =========================

API_BASE_URL = "https://veroot-sendgrid-api-1.onrender.com"

API_TOKEN = None

# Resolve app directory correctly whether running as script or PyInstaller EXE
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN_FILE = os.path.join(APP_DIR, "veroot_token.txt")
LOGO_FILE = os.path.join(APP_DIR, "Veroot-Logo-Mark-2024-White.png")

# =========================
# COLORS
# =========================

DARK_BLUE = "#0A122C"
ORANGE = "#E55820"
SAND = "#F6F4F0"
BACKGROUND_GREY = "#F8F8F9"
STROKE_GREY = "#DADBDF"
ICON_GREY = "#B5B8C0"
WHITE = "#FFFFFF"

auto_refresh = True
event_cache = {}


# =========================
# HELPERS
# =========================

def format_timestamp(ts):
    if not ts:
        return ""

    try:
        return datetime.fromtimestamp(
            int(ts)
        ).strftime("%Y-%m-%d %I:%M:%S %p")
    except Exception:
        return str(ts)


def fetch_events(search="", event="All"):
    response = requests.get(
        f"{API_BASE_URL}/events",
        headers={
            "Authorization": f"Bearer {API_TOKEN}"
        },
        params={
            "search": search,
            "event": event,
            "limit": 500
        },
        timeout=20
    )

    response.raise_for_status()
    return response.json()["events"]


def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        f.write(token.strip())


def load_saved_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return f.read().strip()
    return ""


def test_token(token):
    response = requests.get(
        f"{API_BASE_URL}/events",
        headers={
            "Authorization": f"Bearer {token}"
        },
        params={
            "limit": 1
        },
        timeout=15
    )

    return response.status_code == 200


def show_login_screen():
    global API_TOKEN

    login = tk.Tk()
    login.title("Veroot Login")
    login.geometry("460x220")
    login.configure(bg=BACKGROUND_GREY)

    tk.Label(
        login,
        text="Veroot SendGrid Monitor",
        bg=BACKGROUND_GREY,
        fg=DARK_BLUE,
        font=("Urbanist", 20, "bold")
    ).pack(pady=(25, 8))

    tk.Label(
        login,
        text="Enter your access token",
        bg=BACKGROUND_GREY,
        fg=DARK_BLUE,
        font=("Lato", 11)
    ).pack()

    token_var = tk.StringVar(value=load_saved_token())

    token_entry = ttk.Entry(
        login,
        textvariable=token_var,
        width=48,
        show="*"
    )
    token_entry.pack(pady=14, ipady=5)
    token_entry.focus()

    remember_var = tk.BooleanVar(value=True)

    ttk.Checkbutton(
        login,
        text="Remember token on this computer",
        variable=remember_var
    ).pack()

    status_label = tk.Label(
        login,
        text="",
        bg=BACKGROUND_GREY,
        fg=ORANGE,
        font=("Lato", 10)
    )
    status_label.pack()

    def login_submit():
        global API_TOKEN

        token = token_var.get().strip()

        if not token:
            messagebox.showerror("Missing Token", "Please enter your access token.")
            return

        BUSY_CURSOR = "wait" if sys.platform == "win32" else "watch"

        status_label.config(text="Connecting…")
        login.config(cursor=BUSY_CURSOR)
        login.update()

        try:
            if test_token(token):
                API_TOKEN = token

                if remember_var.get():
                    save_token(token)

                login.destroy()
            else:
                status_label.config(text="")
                login.config(cursor="")
                messagebox.showerror("Invalid Token", "That token was rejected.")

        except Exception as e:
            status_label.config(text="")
            login.config(cursor="")
            messagebox.showerror("Connection Error", str(e))

    ttk.Button(
        login,
        text="Login",
        command=login_submit
    ).pack(pady=12)

    login.bind("<Return>", lambda e: login_submit())
    login.mainloop()


# =========================
# MAIN LOAD
# =========================

def load_events():
    for row in table.get_children():
        table.delete(row)

    event_cache.clear()

    search = search_var.get().strip()
    event_filter = event_var.get()

    try:
        rows = fetch_events(
            search=search,
            event=event_filter
        )

        for row in rows:
            timestamp = row.get("timestamp")
            recipient = row.get("recipient")
            event = row.get("event")
            reason = row.get("reason")

            tag = event or "unknown"

            item_id = table.insert(
                "",
                tk.END,
                values=(
                    format_timestamp(timestamp),
                    recipient or "",
                    event or "",
                    reason or "",
                ),
                tags=(tag,)
            )

            event_cache[item_id] = row

        status_var.set(
            f"Showing {len(rows)} events"
        )

    except Exception as e:
        if not auto_refresh:
            messagebox.showerror("API Error", str(e))
        status_var.set(f"Refresh failed — {type(e).__name__}: {e}")

    if auto_refresh:
        root.after(5000, load_events)


def toggle_auto_refresh():
    global auto_refresh

    auto_refresh = not auto_refresh

    auto_btn.config(
        text=f"Auto Refresh: {'On' if auto_refresh else 'Off'}"
    )

    if auto_refresh:
        load_events()


def clear_search():
    search_var.set("")
    event_var.set("All")
    load_events()


# =========================
# DETAILS POPUP
# =========================

def show_event_details(event_data):
    detail_window = tk.Toplevel(root)
    detail_window.title("Event Details")
    detail_window.geometry("900x650")
    detail_window.configure(bg=BACKGROUND_GREY)

    header = tk.Frame(
        detail_window,
        bg=DARK_BLUE,
        height=80
    )
    header.pack(fill=tk.X)
    header.pack_propagate(False)

    title = tk.Label(
        header,
        text="SendGrid Event Details",
        bg=DARK_BLUE,
        fg=WHITE,
        font=("Urbanist", 22, "bold")
    )
    title.pack(side=tk.LEFT, padx=24)

    event_name = event_data.get("event", "unknown")

    event_badge = tk.Label(
        header,
        text=event_name.upper(),
        bg=ORANGE,
        fg=WHITE,
        font=("Urbanist", 11, "bold"),
        padx=14,
        pady=6
    )
    event_badge.pack(side=tk.RIGHT, padx=24)

    content = tk.Frame(
        detail_window,
        bg=WHITE
    )
    content.pack(
        fill=tk.BOTH,
        expand=True,
        padx=20,
        pady=20
    )

    summary = tk.Frame(
        content,
        bg=WHITE
    )
    summary.pack(
        fill=tk.X,
        padx=16,
        pady=16
    )

    fields = [
        ("Timestamp", format_timestamp(event_data.get("timestamp"))),
        ("Recipient", event_data.get("recipient")),
        ("Event", event_data.get("event")),
        ("Reason", event_data.get("reason")),
        ("SendGrid Event ID", event_data.get("sg_event_id")),
        ("SendGrid Message ID", event_data.get("sg_message_id")),
        ("Message UUID", event_data.get("message_uuid")),
        ("Created At", event_data.get("created_at")),
    ]

    for label, value in fields:
        row_frame = tk.Frame(
            summary,
            bg=WHITE
        )
        row_frame.pack(
            fill=tk.X,
            pady=3
        )

        tk.Label(
            row_frame,
            text=f"{label}:",
            bg=WHITE,
            fg=DARK_BLUE,
            font=("Urbanist", 10, "bold"),
            width=20,
            anchor="w"
        ).pack(side=tk.LEFT)

        tk.Label(
            row_frame,
            text=str(value or ""),
            bg=WHITE,
            fg=DARK_BLUE,
            font=("Lato", 10),
            anchor="w",
            wraplength=600,
            justify=tk.LEFT
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True
        )

    raw_label = tk.Label(
        content,
        text="Raw Webhook JSON",
        bg=WHITE,
        fg=DARK_BLUE,
        font=("Urbanist", 12, "bold")
    )
    raw_label.pack(
        anchor="w",
        padx=16,
        pady=(10, 4)
    )

    text_frame = tk.Frame(
        content,
        bg=WHITE
    )
    text_frame.pack(
        fill=tk.BOTH,
        expand=True,
        padx=16,
        pady=(0, 16)
    )

    raw_text = tk.Text(
        text_frame,
        wrap=tk.NONE,
        bg=BACKGROUND_GREY,
        fg=DARK_BLUE,
        font=("Consolas", 10),
        relief=tk.FLAT,
        padx=10,
        pady=10
    )

    y_scroll = ttk.Scrollbar(
        text_frame,
        orient=tk.VERTICAL,
        command=raw_text.yview
    )

    x_scroll = ttk.Scrollbar(
        content,
        orient=tk.HORIZONTAL,
        command=raw_text.xview
    )

    raw_text.configure(
        yscrollcommand=y_scroll.set,
        xscrollcommand=x_scroll.set
    )

    raw_json = event_data.get("raw_json")

    if isinstance(raw_json, dict):
        raw_display = json.dumps(raw_json, indent=2)
    else:
        raw_display = str(raw_json or "")

    raw_text.insert(
        tk.END,
        raw_display
    )

    raw_text.configure(
        state=tk.DISABLED
    )

    raw_text.pack(
        side=tk.LEFT,
        fill=tk.BOTH,
        expand=True
    )

    y_scroll.pack(
        side=tk.RIGHT,
        fill=tk.Y
    )

    x_scroll.pack(
        fill=tk.X,
        padx=16
    )

    close_btn = ttk.Button(
        detail_window,
        text="Close",
        style="Secondary.TButton",
        command=detail_window.destroy
    )
    close_btn.pack(
        pady=(0, 16)
    )


def on_row_double_click(event):
    selected_item = table.focus()

    if not selected_item:
        return

    event_data = event_cache.get(selected_item)

    if not event_data:
        messagebox.showerror(
            "Missing Event",
            "Could not find details for this row."
        )
        return

    show_event_details(event_data)


# =========================
# UPDATER
# =========================

def download_and_install_update(download_url):
    import tempfile

    try:
        response = requests.get(
            download_url,
            stream=True,
            allow_redirects=True,
            timeout=60
        )

        response.raise_for_status()

        exe_path = os.path.join(
            tempfile.gettempdir(),
            "VerootSendGridMonitor_Update.exe"
        )

        with open(exe_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        if not os.path.exists(exe_path) or os.path.getsize(exe_path) == 0:
            raise Exception("Download failed: update file was not created.")

        messagebox.showinfo(
            "Update Downloaded",
            f"The update was downloaded here:\n\n{exe_path}\n\n"
            "Close this app, then open VerootSendGridMonitor_Update.exe."
        )

    except Exception as e:
        messagebox.showerror("Update Failed", str(e))

def check_for_updates():
    try:
        response = requests.get(
            f"{API_BASE_URL}/latest-version",
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        latest_version = data["version"]
        download_url = data["download_url"]

        print("Current version:", APP_VERSION)
        print("Latest version:", latest_version)

        if Version(latest_version) > Version(APP_VERSION):
            answer = messagebox.askyesno(
                "Update Available",
                f"A new version ({latest_version}) is available.\n\n"
                f"Would you like to download it?"
            )

            if answer:
                download_and_install_update(
                    download_url
                )

    except Exception as e:
        print(
            "Update check failed:",
            str(e)
        )


# =========================
# COPY HELPERS
# =========================

def get_selected_event():
    selected_items = table.selection()

    if not selected_items:
        return None

    selected_item = selected_items[0]

    return event_cache.get(selected_item)


def copy_recipient():
    event_data = get_selected_event()

    if not event_data:
        messagebox.showwarning(
            "No Selection",
            "Please select a row first."
        )
        return

    recipient = event_data.get("recipient", "")

    root.clipboard_clear()
    root.clipboard_append(recipient)
    root.update()

    status_var.set(
        "Recipient copied to clipboard"
    )


def copy_reason():
    event_data = get_selected_event()

    if not event_data:
        messagebox.showwarning(
            "No Selection",
            "Please select a row first."
        )
        return

    reason = event_data.get("reason", "")

    root.clipboard_clear()
    root.clipboard_append(reason)
    root.update()

    status_var.set(
        "Reason copied to clipboard"
    )

def show_context_menu(event):
    row_id = table.identify_row(event.y)

    if row_id:
        table.selection_set(row_id)
        table.focus(row_id)
        context_menu.tk_popup(event.x_root, event.y_root)


def copy_selected_recipient_from_menu():
    copy_recipient()


def copy_selected_reason_from_menu():
    copy_reason()


def view_selected_details_from_menu():
    event_data = get_selected_event()

    if not event_data:
        messagebox.showwarning(
            "No Selection",
            "Please select a row first."
        )
        return

    show_event_details(event_data)
# =========================
# ROOT WINDOW
# =========================

show_login_screen()

root = tk.Tk()

root.after(1000, check_for_updates)

root.title("Veroot SendGrid Monitor")
root.geometry("1450x760")
root.configure(bg=BACKGROUND_GREY)

style = ttk.Style()
style.theme_use("clam")

style.configure(
    "Treeview",
    background=WHITE,
    foreground=DARK_BLUE,
    fieldbackground=WHITE,
    rowheight=34,
    bordercolor=STROKE_GREY,
    borderwidth=1,
    font=("Lato", 10)
)

style.configure(
    "Treeview.Heading",
    background=DARK_BLUE,
    foreground=WHITE,
    font=("Urbanist", 11, "bold"),
    padding=8
)

style.map(
    "Treeview.Heading",
    background=[("active", DARK_BLUE)]
)

style.configure(
    "TFrame",
    background=BACKGROUND_GREY
)

style.configure(
    "TLabel",
    background=BACKGROUND_GREY,
    foreground=DARK_BLUE,
    font=("Lato", 10)
)

style.configure(
    "Veroot.TButton",
    background=ORANGE,
    foreground=WHITE,
    font=("Urbanist", 10, "bold"),
    padding=(14, 8),
    borderwidth=0
)

style.map(
    "Veroot.TButton",
    background=[("active", "#C94718")]
)

style.configure(
    "Secondary.TButton",
    background=WHITE,
    foreground=DARK_BLUE,
    font=("Urbanist", 10, "bold"),
    padding=(14, 8),
    bordercolor=STROKE_GREY,
    borderwidth=1
)

style.map(
    "Secondary.TButton",
    background=[("active", SAND)]
)

style.configure(
    "TCombobox",
    fieldbackground=WHITE,
    background=WHITE,
    foreground=DARK_BLUE,
    padding=6
)

# =========================
# HEADER
# =========================

header = tk.Frame(
    root,
    bg=DARK_BLUE,
    height=120
)
header.pack(fill=tk.X)
header.pack_propagate(False)

brand_block = tk.Frame(
    header,
    bg=DARK_BLUE
)
brand_block.pack(
    fill=tk.BOTH,
    expand=True,
    padx=28,
    pady=18
)

if os.path.exists(LOGO_FILE):
    logo_image = Image.open(LOGO_FILE)
    logo_image = logo_image.resize(
        (76, 76),
        Image.LANCZOS
    )

    logo_photo = ImageTk.PhotoImage(
        logo_image
    )

    logo_label = tk.Label(
        brand_block,
        image=logo_photo,
        bg=DARK_BLUE,
        borderwidth=0
    )
    logo_label.image = logo_photo
    logo_label.pack(
        side=tk.LEFT,
        padx=(0, 16)
    )
else:
    logo_label = tk.Label(
        brand_block,
        text="V",
        bg=DARK_BLUE,
        fg=WHITE,
        font=("Urbanist", 34, "bold")
    )
    logo_label.pack(
        side=tk.LEFT,
        padx=(0, 16)
    )

title_block = tk.Frame(
    brand_block,
    bg=DARK_BLUE
)
title_block.pack(side=tk.LEFT)

title = tk.Label(
    title_block,
    text="VEROOT",
    bg=DARK_BLUE,
    fg=WHITE,
    font=("Urbanist", 34, "bold")
)
title.pack(anchor="w")

subtitle = tk.Label(
    title_block,
    text="SendGrid Delivery Monitor · Hosted API",
    bg=DARK_BLUE,
    fg=SAND,
    font=("Lato", 12)
)
subtitle.pack(anchor="w")

status_pill = tk.Label(
    brand_block,
    text="LIVE",
    bg=ORANGE,
    fg=WHITE,
    font=("Urbanist", 11, "bold"),
    padx=18,
    pady=7
)
status_pill.pack(side=tk.RIGHT)

# =========================
# CONTROLS
# =========================

controls_card = tk.Frame(
    root,
    bg=WHITE,
    highlightbackground=STROKE_GREY,
    highlightthickness=1
)
controls_card.pack(
    fill=tk.X,
    padx=24,
    pady=(22, 12)
)

controls = tk.Frame(
    controls_card,
    bg=WHITE
)
controls.pack(
    fill=tk.X,
    padx=18,
    pady=16
)

search_var = tk.StringVar()

event_var = tk.StringVar(
    value="All"
)

status_var = tk.StringVar(
    value="Ready"
)

tk.Label(
    controls,
    text="Search",
    bg=WHITE,
    fg=DARK_BLUE,
    font=("Urbanist", 11, "bold")
).pack(side=tk.LEFT, padx=(0, 8))

search_entry = ttk.Entry(
    controls,
    textvariable=search_var,
    width=36
)
search_entry.pack(
    side=tk.LEFT,
    padx=(0, 16),
    ipady=5
)
search_entry.bind(
    "<Return>",
    lambda e: load_events()
)

tk.Label(
    controls,
    text="Event",
    bg=WHITE,
    fg=DARK_BLUE,
    font=("Urbanist", 11, "bold")
).pack(side=tk.LEFT, padx=(0, 8))

event_menu = ttk.Combobox(
    controls,
    textvariable=event_var,
    values=[
        "All",
        "processed",
        "delivered",
        "deferred",
        "bounce",
        "dropped",
        "open",
        "click",
        "spamreport",
    ],
    state="readonly",
    width=18
)
event_menu.pack(
    side=tk.LEFT,
    padx=(0, 16),
    ipady=5
)
event_menu.bind(
    "<<ComboboxSelected>>",
    lambda e: load_events()
)

ttk.Button(
    controls,
    text="Search",
    style="Veroot.TButton",
    command=load_events
).pack(side=tk.LEFT, padx=4)

ttk.Button(
    controls,
    text="Clear",
    style="Secondary.TButton",
    command=clear_search
).pack(side=tk.LEFT, padx=4)

auto_btn = ttk.Button(
    controls,
    text="Auto Refresh: On",
    style="Secondary.TButton",
    command=toggle_auto_refresh
)
auto_btn.pack(
    side=tk.LEFT,
    padx=(20, 4)
)

ttk.Button(
    controls,
    text="Refresh Now",
    style="Secondary.TButton",
    command=load_events
).pack(side=tk.LEFT, padx=4)

ttk.Button(
    controls,
    text="Copy Recipient",
    style="Secondary.TButton",
    command=copy_recipient
).pack(side=tk.LEFT, padx=4)

ttk.Button(
    controls,
    text="Copy Reason",
    style="Secondary.TButton",
    command=copy_reason
).pack(side=tk.LEFT, padx=4)

# =========================
# TABLE
# =========================

table_card = tk.Frame(
    root,
    bg=WHITE,
    highlightbackground=STROKE_GREY,
    highlightthickness=1
)
table_card.pack(
    fill=tk.BOTH,
    expand=True,
    padx=24,
    pady=(0, 12)
)

table_frame = ttk.Frame(
    table_card
)
table_frame.pack(
    fill=tk.BOTH,
    expand=True,
    padx=14,
    pady=14
)

columns = (
    "timestamp",
    "recipient",
    "event",
    "reason"
)

table = ttk.Treeview(
    table_frame,
    columns=columns,
    show="headings"
)

for col in columns:
    table.heading(col, text=col.title())

table.column(
    "timestamp",
    width=230,
    stretch=False
)

table.column(
    "recipient",
    width=300,
    stretch=False
)

table.column(
    "event",
    width=140,
    stretch=False
)

table.column(
    "reason",
    width=950,
    stretch=True
)

table.bind("<Double-1>", on_row_double_click)

context_menu = tk.Menu(root, tearoff=0)

context_menu.add_command(
    label="Copy Recipient",
    command=copy_selected_recipient_from_menu
)

context_menu.add_command(
    label="Copy Reason",
    command=copy_selected_reason_from_menu
)

context_menu.add_separator()

context_menu.add_command(
    label="View Details",
    command=view_selected_details_from_menu
)

table.bind("<Button-3>", show_context_menu)

table.tag_configure(
    "delivered",
    background="#EEF8F0",
    foreground=DARK_BLUE
)

table.tag_configure(
    "processed",
    background=WHITE,
    foreground=DARK_BLUE
)

table.tag_configure(
    "deferred",
    background=SAND,
    foreground=DARK_BLUE
)

table.tag_configure(
    "bounce",
    background="#FFECE6",
    foreground=DARK_BLUE
)

table.tag_configure(
    "dropped",
    background="#FFECE6",
    foreground=DARK_BLUE
)

table.tag_configure(
    "spamreport",
    background="#FFECE6",
    foreground=DARK_BLUE
)

table.tag_configure(
    "open",
    background=BACKGROUND_GREY,
    foreground=DARK_BLUE
)

table.tag_configure(
    "click",
    background=BACKGROUND_GREY,
    foreground=DARK_BLUE
)

v_scrollbar = ttk.Scrollbar(
    table_frame,
    orient=tk.VERTICAL,
    command=table.yview
)

h_scrollbar = ttk.Scrollbar(
    table_card,
    orient=tk.HORIZONTAL,
    command=table.xview
)

table.configure(
    yscrollcommand=v_scrollbar.set,
    xscrollcommand=h_scrollbar.set
)

table.pack(
    side=tk.LEFT,
    fill=tk.BOTH,
    expand=True
)

v_scrollbar.pack(
    side=tk.RIGHT,
    fill=tk.Y
)

h_scrollbar.pack(
    fill=tk.X,
    padx=14,
    pady=(0, 12)
)

# =========================
# FOOTER
# =========================

footer = tk.Frame(
    root,
    bg=BACKGROUND_GREY
)
footer.pack(
    fill=tk.X,
    padx=24,
    pady=(0, 16)
)

tk.Label(
    footer,
    textvariable=status_var,
    bg=BACKGROUND_GREY,
    fg=DARK_BLUE,
    font=("Lato", 10)
).pack(side=tk.LEFT)

tk.Label(
    footer,
    text="Hosted on Render · PostgreSQL · Authenticated API",
    bg=BACKGROUND_GREY,
    fg=ICON_GREY,
    font=("Lato", 10)
).pack(side=tk.RIGHT)

load_events()

root.mainloop()
