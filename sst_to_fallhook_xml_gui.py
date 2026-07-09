#!/usr/bin/env python3
"""Batch Tkinter GUI for the SST to FallHook XML converter."""

from __future__ import annotations

import json
import os
import pathlib
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from sst_to_fallhook_xml import convert_file, default_addon, default_output_path, infer_addon_from_path, parse_language_pair, read_sst


APP_TITLE = "SST to FallHook XML"


def settings_path() -> pathlib.Path:
    base = os.environ.get("APPDATA")
    if base:
        return pathlib.Path(base) / "SST2FallHookXML" / "settings.json"
    return pathlib.Path.home() / ".sst2fallhookxml" / "settings.json"


def load_settings() -> dict[str, str]:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_settings(data: dict[str, str]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class ConverterApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.grid(row=0, column=0, sticky="nsew")

        master.title(APP_TITLE)
        master.minsize(760, 460)
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)

        self.settings = load_settings()
        self.output_dir_var = tk.StringVar(value=self.settings.get("output_dir", ""))
        self.lang_pair_var = tk.StringVar(value=self.settings.get("language_pair", "en_ko"))
        self.status_var = tk.StringVar(value="Select one or more SST files.")
        self.result_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.input_files: list[pathlib.Path] = []

        self._build_ui()
        self._poll_queue()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(1, weight=1)

        ttk.Button(toolbar, text="Add SST Files", command=self.add_files).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="Clear", command=self.clear_files).grid(row=0, column=1, sticky="w")

        columns = ("file", "addon", "entries")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=12)
        self.tree.heading("file", text="SST File")
        self.tree.heading("addon", text="Detected Plugin")
        self.tree.heading("entries", text="Entries")
        self.tree.column("file", width=430, anchor="w")
        self.tree.column("addon", width=190, anchor="w")
        self.tree.column("entries", width=80, anchor="e")
        self.tree.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        output = ttk.LabelFrame(self, text="Output", padding=12)
        output.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        output.columnconfigure(1, weight=1)
        ttk.Label(output, text="Folder").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(output, textvariable=self.output_dir_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(output, text="Browse", command=self.choose_output_dir).grid(row=0, column=2)
        ttk.Label(output, text="Language").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(output, textvariable=self.lang_pair_var, width=16).grid(row=1, column=1, sticky="w", pady=(8, 0))

        footer = ttk.Frame(self)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")
        self.convert_button = ttk.Button(footer, text="Convert All", command=self.convert_all)
        self.convert_button.grid(row=0, column=1, padx=(8, 0))

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Select SST dictionaries",
            filetypes=(("xTranslator SST", "*.sst"), ("All files", "*.*")),
        )
        if not paths:
            return

        existing = {path.resolve() for path in self.input_files}
        added = 0
        for raw_path in paths:
            path = pathlib.Path(raw_path)
            resolved = path.resolve()
            if resolved in existing:
                continue
            existing.add(resolved)
            self.input_files.append(path)
            self._insert_file(path)
            added += 1

        if added and not self.output_dir_var.get():
            self.output_dir_var.set(str(self.input_files[0].parent))
        self.status_var.set(f"{len(self.input_files)} SST file(s) selected.")

    def _insert_file(self, path: pathlib.Path) -> None:
        try:
            sst = read_sst(path)
            addon = default_addon(sst.plugins, infer_addon_from_path(path))
            entries = str(len(sst.entries))
        except Exception as exc:
            addon = f"Read error: {exc}"
            entries = "-"
        self.tree.insert("", "end", values=(str(path), addon, entries))

    def clear_files(self) -> None:
        self.input_files.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.status_var.set("Select one or more SST files.")

    def choose_output_dir(self) -> None:
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir_var.set(path)
            self.save_current_settings()

    def convert_all(self) -> None:
        if not self.input_files:
            messagebox.showwarning(APP_TITLE, "Choose at least one SST file.")
            return

        output_dir = pathlib.Path(self.output_dir_var.get().strip('" '))
        if not output_dir:
            messagebox.showwarning(APP_TITLE, "Choose an output folder.")
            return
        try:
            source_lang, dest_lang = parse_language_pair(self.lang_pair_var.get())
        except ValueError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return

        self.save_current_settings()
        self.convert_button.configure(state="disabled")
        self.status_var.set("Converting...")
        thread = threading.Thread(
            target=self._convert_worker,
            args=(list(self.input_files), output_dir, source_lang, dest_lang),
            daemon=True,
        )
        thread.start()

    def _convert_worker(
        self,
        input_files: list[pathlib.Path],
        output_dir: pathlib.Path,
        source_lang: str,
        dest_lang: str,
    ) -> None:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.result_queue.put(("error", f"Could not create output folder: {exc}"))
            return

        ok = 0
        failures: list[str] = []
        for input_path in input_files:
            output_path = default_output_path(input_path, output_dir)
            try:
                convert_file(
                    input_path=input_path,
                    output_path=output_path,
                    source_lang=source_lang,
                    dest_lang=dest_lang,
                    formid_mode="raw",
                    include_sid=True,
                )
                ok += 1
            except Exception as exc:
                failures.append(f"{input_path.name}: {exc}")

        if failures:
            text = f"Converted {ok}/{len(input_files)} file(s).\n\n" + "\n".join(failures[:8])
            if len(failures) > 8:
                text += f"\n...and {len(failures) - 8} more."
            self.result_queue.put(("error", text))
        else:
            self.result_queue.put(("ok", f"Converted {ok} file(s) to {output_dir}"))

    def _poll_queue(self) -> None:
        try:
            kind, text = self.result_queue.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_queue)
            return

        self.convert_button.configure(state="normal")
        self.status_var.set(text.splitlines()[0])
        if kind == "ok":
            messagebox.showinfo(APP_TITLE, text)
        else:
            messagebox.showerror(APP_TITLE, text)
        self.after(100, self._poll_queue)

    def save_current_settings(self) -> None:
        try:
            source_lang, dest_lang = parse_language_pair(self.lang_pair_var.get())
            language_pair = f"{source_lang}_{dest_lang}"
        except ValueError:
            language_pair = self.lang_pair_var.get().strip()
        save_settings(
            {
                "language_pair": language_pair,
                "output_dir": self.output_dir_var.get().strip(),
            }
        )

    def on_close(self) -> None:
        self.save_current_settings()
        self.master.destroy()


def main() -> None:
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
