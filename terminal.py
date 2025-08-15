#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
My Personal Terminal - Version 1 (Core)
Features:
- Accounts: register/login with password (hashed), optional PIN lock
- Data categories: notes, contacts, passwords, projects
- Data ops: save/view/edit/delete/search (per category)
- System: run OS commands, clear screen
- Utilities: date/time (now), calculator (safe)
- Web: open URL or alias, manage web shortcuts
- Look & Feel: themes, ASCII banner, custom prompt
- History: persists between sessions
"""

import cmd
import os
import sys
import json
import hashlib
import time
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

# ---- Color support (ANSI) + Windows compatibility ----
try:
    import readline  # History + arrow keys (Unix/macOS, sometimes on Windows via pyreadline3)
except Exception:
    readline = None

try:
    # On Windows, enable ANSI colors
    import colorama
    colorama.just_fix_windows_console()
except Exception:
    pass

# ---- Constants & Paths ----
APP_NAME = "MyPersonalTerminal"
DEFAULT_CATEGORIES = ["notes", "contacts", "passwords", "projects"]

HOME = Path.home()
APP_ROOT = HOME / f".{APP_NAME.lower()}"
USERS_FILE = APP_ROOT / "users.json"           # stores users: {username: {password_hash, pin_hash|None}}
HISTORY_FILE = APP_ROOT / "history.txt"        # command history
CONFIG_FILE_TPL = "config.json"                # per-user config in user folder
WEB_SHORTCUTS_FILE_TPL = "web_shortcuts.json"  # per-user web aliases
BANNER = r"""
 __  __        ____                      _            _ 
|  \/  | __ _ / ___| _ __ ___  __ _  ___| | ___   ___| |
| |\/| |/ _` |\___ \| '__/ _ \/ _` |/ __| |/ _ \ / __| |
| |  | | (_| | ___) | | |  __/ (_| | (__| | (_) | (__| |
|_|  |_|\__,_||____/|_|  \___|\__,_|\___|_|\___/ \___|_|
"""

# ---- Helpers ----
def shash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def ensure_dirs(path: Path):
    path.mkdir(parents=True, exist_ok=True)

def safe_json_load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def safe_json_save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def user_root(username: str) -> Path:
    return APP_ROOT / "users" / username

def category_dir(username: str, category: str) -> Path:
    return user_root(username) / "data" / category

def user_config_path(username: str) -> Path:
    return user_root(username) / CONFIG_FILE_TPL

def web_shortcuts_path(username: str) -> Path:
    return user_root(username) / WEB_SHORTCUTS_FILE_TPL

def normalize_title(title: str) -> str:
    return "_".join(title.strip().split())

def warn_passwords_category():
    print("\033[93m[Note]\033[0m You are using the 'passwords' category. Encryption is not enabled in v1. "
          "Avoid storing real passwords here. (We can add encryption in v2.)")

def load_history():
    if readline is None:
        return
    ensure_dirs(APP_ROOT)
    try:
        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
    except Exception:
        pass

def save_history():
    if readline is None:
        return
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except Exception:
        pass

# ---- Safe calculator using AST ----
import ast
import operator as op

SAFE_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow,
    ast.USub: op.neg, ast.UAdd: op.pos, ast.BitXor: op.xor,
    ast.LShift: op.lshift, ast.RShift: op.rshift, ast.BitAnd: op.and_,
    ast.BitOr: op.or_
}

def safe_eval_expr(expr: str):
    def _eval(node):
        if isinstance(node, ast.Num):   # Py<3.8
            return node.n
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numbers allowed.")
        if isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPS:
            return SAFE_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPS:
            return SAFE_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("Unsupported expression.")
    tree = ast.parse(expr, mode="eval")
    return _eval(tree.body)

# ---- Terminal Class ----
class MyPersonalTerminal(cmd.Cmd):
    ruler = "-"
    intro = ""  # set dynamically after login to include banner
    prompt = "myterm> "  # set dynamically via theme/prompt config

    def __init__(self):
        super().__init__()
        ensure_dirs(APP_ROOT)
        self.username = None
        self.authenticated = False
        self.pin_hash = None
        self.theme = "dark"  # default
        self.custom_prompt = "myterm> "
        self.last_activity_ts = time.time()

    # ---------- Auth flow ----------
    def preloop(self):
        load_history()
        self._load_users()
        self._login_or_register()
        self._load_user_config()
        self._apply_theme_to_prompt()
        self._print_banner()

    def postloop(self):
        save_history()

    def _load_users(self):
        ensure_dirs(APP_ROOT)
        self.users = safe_json_load(USERS_FILE, {})

    def _save_users(self):
        safe_json_save(USERS_FILE, self.users)

    def _login_or_register(self):
        print("\033[96mWelcome to your Personal Terminal.\033[0m")
        if not self.users:
            print("\033[92mNo users found. Let's create your account.\033[0m")
            self._register_user()
        else:
            while True:
                choice = input("Do you want to (l)ogin or (r)egister? [l/r]: ").strip().lower()
                if choice in ("l", "r"):
                    break
            if choice == "r":
                self._register_user()
            else:
                self._login_user()

    def _register_user(self):
        while True:
            username = input("Choose a username: ").strip()
            if not username:
                print("Username cannot be empty.")
                continue
            if username in self.users:
                print("User already exists. Pick another name.")
                continue
            break
        while True:
            pw = input("Choose a password: ").strip()
            if not pw:
                print("Password cannot be empty.")
                continue
            confirm = input("Confirm password: ").strip()
            if pw != confirm:
                print("Passwords do not match.")
                continue
            break
        set_pin = input("Set a 4-8 digit PIN? (optional) [y/N]: ").strip().lower() == "y"
        pin_hash = None
        if set_pin:
            while True:
                pin = input("Enter PIN (4-8 digits): ").strip()
                if pin.isdigit() and 4 <= len(pin) <= 8:
                    pin_hash = shash(pin)
                    break
                print("Invalid PIN. Use 4-8 digits.")
        self.users[username] = {"password_hash": shash(pw), "pin_hash": pin_hash}
        self._save_users()

        # Initialize user folders and config
        self.username = username
        self.authenticated = True
        self.pin_hash = pin_hash
        self._init_user_space()
        print(f"\033[92mUser '{username}' registered and logged in.\033[0m")

    def _login_user(self):
        for _ in range(5):
            username = input("Username: ").strip()
            pw = input("Password: ").strip()
            if username in self.users and self.users[username]["password_hash"] == shash(pw):
                self.username = username
                self.authenticated = True
                self.pin_hash = self.users[username].get("pin_hash")
                print("\033[92mLogin successful.\033[0m")
                self._init_user_space()
                return
            print("\033[91mInvalid credentials.\033[0m")
        print("Too many failed attempts. Exiting.")
        sys.exit(1)

    def _init_user_space(self):
        # Create user directories & default categories
        uroot = user_root(self.username)
        ensure_dirs(uroot / "data")
        for cat in DEFAULT_CATEGORIES:
            ensure_dirs(category_dir(self.username, cat))
        # Create default config if missing
        cfg_path = user_config_path(self.username)
        if not cfg_path.exists():
            cfg = {"theme": "dark", "prompt": "myterm> ", "banner": True}
            safe_json_save(cfg_path, cfg)
        # Create web shortcuts file if missing
        wpath = web_shortcuts_path(self.username)
        if not wpath.exists():
            safe_json_save(wpath, {"yt": "https://youtube.com", "gg": "https://google.com"})

    def _load_user_config(self):
        cfg = safe_json_load(user_config_path(self.username), {"theme": "dark", "prompt": "myterm> ", "banner": True})
        self.theme = cfg.get("theme", "dark")
        self.custom_prompt = cfg.get("prompt", "myterm> ")
        self.banner_on = bool(cfg.get("banner", True))

    def _save_user_config(self):
        cfg = {"theme": self.theme, "prompt": self.custom_prompt, "banner": self.banner_on}
        safe_json_save(user_config_path(self.username), cfg)

    def _print_banner(self):
        if self.banner_on:
            print("\033[95m" + BANNER + "\033[0m")
            print("Type \033[96mhelp\033[0m or \033[96m?\033[0m to see commands.\n")

    def _apply_theme_to_prompt(self):
        base = self.custom_prompt.rstrip() + " "
        if self.theme == "light":
            self.prompt = f"\033[94m{base}\033[0m"
        elif self.theme == "hacker":
            self.prompt = f"\033[92m{base}\033[0m"
        else:  # dark/default
            self.prompt = f"\033[96m{base}\033[0m"

    # ---------- Activity tracking ----------
    def precmd(self, line: str) -> str:
        self.last_activity_ts = time.time()
        # Persist manual history if readline missing
        try:
            if readline is None and line.strip():
                with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                    f.write(line.strip() + "\n")
        except Exception:
            pass
        return line

    # ---------- Utility commands ----------
    def do_now(self, arg):
        """Show current date & time. Usage: now"""
        print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def do_calc(self, arg):
        """Calculator (safe). Usage: calc <expression>   e.g., calc (2+3)*4"""
        expr = arg.strip()
        if not expr:
            print("Usage: calc <expression>")
            return
        try:
            result = safe_eval_expr(expr)
            print(result)
        except Exception as e:
            print(f"\033[91mError:\033[0m {e}")

    def do_clear(self, arg):
        """Clear the screen."""
        subprocess.run("cls" if os.name == "nt" else "clear", shell=True)

    # ---------- Web commands ----------
    def do_web(self, arg):
        """Open a URL or alias. Usage: web <url|alias>"""
        target = arg.strip()
        if not target:
            print("Usage: web <url|alias>")
            return
        aliases = safe_json_load(web_shortcuts_path(self.username), {})
        url = aliases.get(target, target)
        if not (url.startswith("http://") or url.startswith("https://")):
            url = "http://" + url
        print(f"Opening {url} ...")
        webbrowser.open(url)

    def do_webadd(self, arg):
        """Add/update web alias. Usage: webadd <alias> <url>"""
        parts = arg.split()
        if len(parts) < 2:
            print("Usage: webadd <alias> <url>")
            return
        alias, url = parts[0], " ".join(parts[1:])
        aliases = safe_json_load(web_shortcuts_path(self.username), {})
        aliases[alias] = url
        safe_json_save(web_shortcuts_path(self.username), aliases)
        print(f"Alias '{alias}' -> {url} saved.")

    def do_weblist(self, arg):
        """List web aliases."""
        aliases = safe_json_load(web_shortcuts_path(self.username), {})
        if not aliases:
            print("(no aliases)")
            return
        for k, v in aliases.items():
            print(f"{k:10s} -> {v}")

    def do_webdel(self, arg):
        """Delete a web alias. Usage: webdel <alias>"""
        alias = arg.strip()
        if not alias:
            print("Usage: webdel <alias>")
            return
        aliases = safe_json_load(web_shortcuts_path(self.username), {})
        if alias in aliases:
            del aliases[alias]
            safe_json_save(web_shortcuts_path(self.username), aliases)
            print(f"Alias '{alias}' deleted.")
        else:
            print("Alias not found.")

    # ---------- Theme & Prompt ----------
    def do_theme(self, arg):
        """Set theme. Usage: theme [dark|light|hacker]"""
        choice = arg.strip().lower()
        if choice not in ("dark", "light", "hacker"):
            print("Themes: dark, light, hacker")
            return
        self.theme = choice
        self._save_user_config()
        self._apply_theme_to_prompt()
        print(f"Theme set to {choice}.")

    def do_prompt(self, arg):
        """Set prompt text. Usage: prompt <text>"""
        text = arg.strip()
        if not text:
            print("Usage: prompt <text>")
            return
        self.custom_prompt = text
        self._save_user_config()
        self._apply_theme_to_prompt()
        print("Prompt updated.")

    def do_banner(self, arg):
        """Toggle banner on/off. Usage: banner [on|off]"""
        val = arg.strip().lower()
        if val not in ("on", "off"):
            print("Usage: banner [on|off]")
            return
        self.banner_on = (val == "on")
        self._save_user_config()
        print(f"Banner set to {val}.")

    # ---------- Security: PIN lock ----------
    def do_lock(self, arg):
        """Lock the terminal (requires PIN if set). Usage: lock"""
        if not self.pin_hash:
            print("No PIN set. Use 'setpin' to create one.")
            return
        print("Locked. Enter PIN to continue.")
        while True:
            pin = input("PIN: ").strip()
            if shash(pin) == self.pin_hash:
                print("Unlocked.")
                return
            print("Wrong PIN.")

    def do_setpin(self, arg):
        """Set or change PIN. Usage: setpin"""
        if self.pin_hash:
            old = input("Enter old PIN: ").strip()
            if shash(old) != self.pin_hash:
                print("Incorrect old PIN.")
                return
        while True:
            pin = input("New PIN (4-8 digits): ").strip()
            if pin.isdigit() and 4 <= len(pin) <= 8:
                confirm = input("Confirm PIN: ").strip()
                if confirm == pin:
                    self.pin_hash = shash(pin)
                    self.users[self.username]["pin_hash"] = self.pin_hash
                    self._save_users()
                    print("PIN updated.")
                    return
                print("PINs do not match.")
            else:
                print("Invalid PIN format.")

    def do_clearpin(self, arg):
        """Remove PIN (no lock). Usage: clearpin"""
        if not self.pin_hash:
            print("No PIN set.")
            return
        pin = input("Enter current PIN: ").strip()
        if shash(pin) == self.pin_hash:
            self.pin_hash = None
            self.users[self.username]["pin_hash"] = None
            self._save_users()
            print("PIN cleared.")
        else:
            print("Wrong PIN.")

    # ---------- Personal Data Management ----------
    def do_categories(self, arg):
        """List or manage categories. Usage: categories [list|add <name>|del <name>]"""
        parts = arg.split()
        if not parts or parts[0] == "list":
            path = user_root(self.username) / "data"
            cats = sorted([p.name for p in path.iterdir() if p.is_dir()])
            for c in cats:
                print("- " + c)
            return
        if parts[0] == "add" and len(parts) >= 2:
            name = parts[1].strip()
            ensure_dirs(category_dir(self.username, name))
            print(f"Category '{name}' added.")
            return
        if parts[0] == "del" and len(parts) >= 2:
            name = parts[1].strip()
            cdir = category_dir(self.username, name)
            if not cdir.exists():
                print("Category not found.")
                return
            if any(cdir.iterdir()):
                print("Category not empty. Delete files first.")
                return
            cdir.rmdir()
            print(f"Category '{name}' deleted.")
            return
        print("Usage: categories [list|add <name>|del <name>]")

    def _write_entry(self, category: str, title: str, content: str):
        cdir = category_dir(self.username, category)
        if not cdir.exists():
            print("Category does not exist. Create it with: categories add " + category)
            return False
        fname = f"{normalize_title(title)}.txt"
        fpath = cdir / fname
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = f"{content.rstrip()}\n\n---\nSaved on: {ts}\nTitle: {title}\nCategory: {category}\n"
        fpath.write_text(body, encoding="utf-8")
        return True

    def do_save(self, arg):
        """Save entry. Usage: save <category> <title>|<content>"""
        if "|" not in arg:
            print("Usage: save <category> <title>|<content>")
            return
        head, content = arg.split("|", 1)
        parts = head.strip().split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: save <category> <title>|<content>")
            return
        category, title = parts[0], parts[1]
        if category == "passwords":
            warn_passwords_category()
        ok = self._write_entry(category, title, content)
        if ok:
            print(f"\033[92mSaved:\033[0m {category}/{normalize_title(title)}.txt")

    def do_view(self, arg):
        """View entry/entries. Usage: view <category> <title> | view <category> all"""
        parts = arg.split(maxsplit=2)
        if len(parts) < 2:
            print("Usage: view <category> <title|all>")
            return
        category, rest = parts[0], " ".join(parts[1:])
        cdir = category_dir(self.username, category)
        if not cdir.exists():
            print("Category not found.")
            return
        if rest.lower() == "all":
            files = sorted(p.name for p in cdir.glob("*.txt"))
            if not files:
                print("(no entries)")
                return
            for name in files:
                print(f"- {name}")
            return
        fname = f"{normalize_title(rest)}.txt"
        fpath = cdir / fname
        if not fpath.exists():
            print("Entry not found.")
            return
        print("\033[92m" + fpath.read_text(encoding="utf-8") + "\033[0m")

    def do_delete(self, arg):
        """Delete entry. Usage: delete <category> <title>"""
        parts = arg.split(maxsplit=2)
        if len(parts) < 2:
            print("Usage: delete <category> <title>")
            return
        category, title = parts[0], " ".join(parts[1:])
        cdir = category_dir(self.username, category)
        fname = f"{normalize_title(title)}.txt"
        fpath = cdir / fname
        if fpath.exists():
            fpath.unlink()
            print(f"Deleted {category}/{fname}")
        else:
            print("Entry not found.")

    def do_edit(self, arg):
        """Edit entry (replace content). Usage: edit <category> <title>|<new_content>"""
        if "|" not in arg:
            print("Usage: edit <category> <title>|<new_content>")
            return
        head, new_content = arg.split("|", 1)
        parts = head.strip().split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: edit <category> <title>|<new_content>")
            return
        category, title = parts[0], parts[1]
        cdir = category_dir(self.username, category)
        fname = f"{normalize_title(title)}.txt"
        fpath = cdir / fname
        if not fpath.exists():
            print("Entry not found.")
            return
        ok = self._write_entry(category, title, new_content)
        if ok:
            print("Entry updated.")

    def do_search(self, arg):
        """Search entries by keyword. Usage: search <category> <keyword>"""
        parts = arg.strip().split(maxsplit=1)
        if len(parts) < 2:
            print("Usage: search <category> <keyword>")
            return
        category, keyword = parts[0], parts[1].lower()
        cdir = category_dir(self.username, category)
        if not cdir.exists():
            print("Category not found.")
            return
        hits = []
        for f in cdir.glob("*.txt"):
            try:
                text = f.read_text(encoding="utf-8").lower()
                if keyword in text:
                    hits.append(f.name)
            except Exception:
                pass
        if hits:
            print("Matches:")
            for h in hits:
                print("- " + h)
        else:
            print("(no matches)")

    # ---------- System command fallback ----------
    def default(self, line: str):
        """Run OS command when not a built-in."""
        if not line.strip():
            return
        try:
            result = subprocess.run(line, shell=True, capture_output=True, text=True)
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="")
        except Exception as e:
            print(f"\033[91mCommand failed:\033[0m {e}")

    # ---------- Exit ----------
    def do_exit(self, arg):
        """Exit the terminal."""
        print("\033[91mGoodbye!\033[0m")
        return True

    def do_quit(self, arg):
        """Exit the terminal."""
        return self.do_exit(arg)

# ---- Main ----
def main():
    try:
        MyPersonalTerminal().cmdloop()
    except KeyboardInterrupt:
        print("\n\033[91mInterrupted. Bye!\033[0m")

if __name__ == "__main__":
    main()
