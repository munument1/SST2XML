#!/usr/bin/env python3
"""Convert an xTranslator SST dictionary to FallHook-compatible XML.

The generated XML keeps the xTranslator SSTXMLRessources shape, but writes
bracket FormIDs in the EDID element so FallHook can resolve them without
EditorIDs.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import struct
import sys
from typing import Iterable
from xml.sax.saxutils import escape, quoteattr


class SstParseError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class SstEntry:
    offset: int
    form_id: int
    rec: str
    layout: str
    rec_id: int
    rec_id_max: int
    string_id: int
    flags: int
    source: str
    dest: str
    tail: bytes


@dataclasses.dataclass(frozen=True)
class SstFile:
    format: str
    plugins: list[str]
    entries: list[SstEntry]


def read_u16(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 2 > len(data):
        raise SstParseError(f"unexpected EOF while reading uint16 at 0x{pos:X}")
    return struct.unpack_from("<H", data, pos)[0], pos + 2


def read_u32(data: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(data):
        raise SstParseError(f"unexpected EOF while reading uint32 at 0x{pos:X}")
    return struct.unpack_from("<I", data, pos)[0], pos + 4


def decode_utf16le(raw: bytes, pos: int) -> str:
    try:
        return raw.decode("utf-16le").rstrip("\x00")
    except UnicodeDecodeError as exc:
        raise SstParseError(f"invalid UTF-16LE text near 0x{pos:X}: {exc}") from exc


def is_record_signature(raw: bytes) -> bool:
    if len(raw) != 8:
        return False
    return all(
        0x30 <= ch <= 0x39 or 0x41 <= ch <= 0x5A or ch in (0x2A, 0x5F)
        for ch in raw
    )


def record_layout_at(data: bytes, pos: int) -> str | None:
    if pos + 31 <= len(data) and is_record_signature(data[pos + 4 : pos + 12]):
        return "standard"
    if pos + 35 <= len(data) and is_record_signature(data[pos + 8 : pos + 16]):
        return "extended"
    return None


def looks_like_record(data: bytes, pos: int) -> bool:
    return record_layout_at(data, pos) is not None


def find_record_start(data: bytes, pos: int) -> int:
    for candidate in range(pos, min(len(data), pos + 64)):
        if looks_like_record(data, candidate):
            return candidate
    raise SstParseError(f"expected SST record at 0x{pos:X}")


def read_sst(path: pathlib.Path) -> SstFile:
    data = path.read_bytes()
    plugins: list[str] = []
    if len(data) < 14 or data[:3] != b"SSU" or data[3] not in (ord("8"), ord("9")):
        raise SstParseError("not a supported SSU8/SSU9 SST file")

    file_format = data[:4].decode("ascii")
    if file_format == "SSU9":
        pos = 5
        plugin_count, pos = read_u32(data, pos)
        for _ in range(plugin_count):
            length, pos = read_u32(data, pos)
            if pos + length > len(data):
                raise SstParseError(f"plugin name length exceeds file size at 0x{pos:X}")
            plugins.append(decode_utf16le(data[pos : pos + length], pos))
            pos += length

        if pos < len(data):
            pos = find_record_start(data, pos)
    else:
        pos = find_record_start(data, 14)

    entries: list[SstEntry] = []
    while pos < len(data):
        layout = record_layout_at(data, pos)
        if layout is None:
            raise SstParseError(f"expected SST record at 0x{pos:X}")

        offset = pos
        form_id, pos = read_u32(data, pos)
        if layout == "extended":
            _, pos = read_u32(data, pos)
        rec_raw = data[pos : pos + 8]
        if not is_record_signature(rec_raw):
            raise SstParseError(f"invalid REC signature at 0x{pos:X}")
        rec = rec_raw.decode("ascii")
        pos += 8

        rec_id, pos = read_u16(data, pos)
        rec_id_max, pos = read_u16(data, pos)
        string_id, pos = read_u32(data, pos)
        flags, pos = read_u16(data, pos)

        source_len, pos = read_u32(data, pos)
        if pos + source_len > len(data):
            raise SstParseError(f"source length exceeds file size at 0x{offset:X}")
        source = decode_utf16le(data[pos : pos + source_len], pos)
        pos += source_len

        dest_len, pos = read_u32(data, pos)
        if pos + dest_len > len(data):
            raise SstParseError(f"dest length exceeds file size at 0x{offset:X}")
        dest = decode_utf16le(data[pos : pos + dest_len], pos)
        pos += dest_len

        tail = b""
        if pos + 5 <= len(data) and looks_like_record(data, pos + 5):
            tail = data[pos : pos + 5]
            pos += 5
        elif pos + 5 == len(data):
            tail = data[pos : pos + 5]
            pos += 5

        entries.append(
            SstEntry(
                offset=offset,
                form_id=form_id,
                rec=rec,
                layout=layout,
                rec_id=rec_id,
                rec_id_max=rec_id_max,
                string_id=string_id,
                flags=flags,
                source=source,
                dest=dest,
                tail=tail,
            )
        )

    return SstFile(format=file_format, plugins=plugins, entries=entries)


def rec_to_xml(rec: str) -> str:
    if len(rec) != 8:
        return rec
    return f"{rec[:4]}:{rec[4:]}"


def form_id_for_xml(form_id: int, mode: str) -> int:
    if mode == "raw":
        return form_id
    if mode == "light":
        return form_id & 0x00000FFF
    return form_id & 0x00FFFFFF


def string_attrs(entry: SstEntry, include_sid: bool) -> str:
    attrs = [('List', '0')]
    if include_sid and entry.string_id:
        attrs.append(('sID', f"{entry.string_id:08X}"))
    partial = entry.tail[0] if entry.tail else 0
    if partial:
        attrs.append(('Partial', str(partial)))
    return " ".join(f"{name}={quoteattr(value)}" for name, value in attrs)


def rec_attrs(entry: SstEntry) -> str:
    if entry.rec_id == 0 and entry.rec_id_max == 0:
        return ""
    return f' id="{entry.rec_id}" idMax="{entry.rec_id_max}"'


def write_xml(
    sst: SstFile,
    output: pathlib.Path,
    addon: str,
    source_lang: str,
    dest_lang: str,
    formid_mode: str,
    include_sid: bool,
) -> None:
    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        "<SSTXMLRessources>",
        "  <Params>",
        f"    <Addon>{escape(addon)}</Addon>",
        f"    <Source>{escape(source_lang)}</Source>",
        f"    <Dest>{escape(dest_lang)}</Dest>",
        "    <Version>2</Version>",
        "  </Params>",
        "  <Content>",
    ]

    for entry in sst.entries:
        attrs = string_attrs(entry, include_sid)
        xml_form_id = form_id_for_xml(entry.form_id, formid_mode)
        lines.extend(
            [
                f"    <String {attrs}>",
                f"      <EDID>[{xml_form_id:08X}]</EDID>",
                f"      <REC{rec_attrs(entry)}>{escape(rec_to_xml(entry.rec))}</REC>",
                f"      <Source>{escape(entry.source)}</Source>",
                f"      <Dest>{escape(entry.dest)}</Dest>",
                "    </String>",
            ]
        )

    lines.extend(["  </Content>", "</SSTXMLRessources>", ""])
    output.write_text("\n".join(lines), encoding="utf-8")


def default_addon(plugins: Iterable[str], fallback: str) -> str:
    names = [name for name in plugins if name]
    return names[-1] if names else fallback


def infer_languages(path: pathlib.Path) -> tuple[str, str]:
    match = re.search(r"(^|[_\-.])([a-z]{2,3})[_\-]([a-z]{2,3})(?=($|[_\-.]))", path.stem, re.IGNORECASE)
    if not match:
        return "en", "ko"
    return match.group(2).lower(), match.group(3).lower()


def parse_language_pair(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*([a-zA-Z]{2,3})[_\-]([a-zA-Z]{2,3})\s*", value)
    if not match:
        raise ValueError("language pair must look like en_ko, en_ja, en_ru, or fr_de")
    return match.group(1).lower(), match.group(2).lower()


def strip_language_pair(stem: str) -> str:
    return re.sub(r"[_\-][a-zA-Z]{2,3}[_\-][a-zA-Z]{2,3}$", "", stem)


def infer_addon_from_path(path: pathlib.Path) -> str:
    base = strip_language_pair(path.stem)
    known_esm = {
        "fallout4",
        "dlcrobot",
        "dlccoast",
        "dlcnukaworld",
        "dlcworkshop01",
        "dlcworkshop02",
        "dlcworkshop03",
    }
    extension = ".esm" if base.lower() in known_esm else ".esp"
    return base + extension


def default_output_path(input_path: pathlib.Path, output_dir: pathlib.Path | None = None) -> pathlib.Path:
    base_dir = output_dir if output_dir is not None else input_path.parent
    return base_dir / input_path.with_suffix(".xml").name


def convert_file(
    input_path: pathlib.Path,
    output_path: pathlib.Path,
    addon: str | None = None,
    source_lang: str | None = None,
    dest_lang: str | None = None,
    formid_mode: str = "raw",
    include_sid: bool = True,
) -> tuple[int, str]:
    sst = read_sst(input_path)
    resolved_addon = addon or default_addon(sst.plugins, infer_addon_from_path(input_path))
    inferred_source, inferred_dest = infer_languages(input_path)
    write_xml(
        sst=sst,
        output=output_path,
        addon=resolved_addon,
        source_lang=source_lang or inferred_source,
        dest_lang=dest_lang or inferred_dest,
        formid_mode=formid_mode,
        include_sid=include_sid,
    )
    return len(sst.entries), resolved_addon


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert xTranslator .sst dictionaries to FallHook XML."
    )
    parser.add_argument("input", type=pathlib.Path, nargs="+", help="Input .sst file(s)")
    parser.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        help="Output .xml file for one input, or output directory for multiple inputs.",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        help="Directory for generated XML files.",
    )
    parser.add_argument(
        "--addon",
        help="Addon/plugin name for XML Params. Defaults to the last plugin in the SST header.",
    )
    parser.add_argument("--source", help="Advanced: source language code")
    parser.add_argument("--dest", help="Advanced: destination language code")
    parser.add_argument("--lang-pair", help="Language pair for XML Params, e.g. en_ko or en_ru")
    parser.add_argument(
        "--formid-mode",
        choices=("local", "raw", "light"),
        default="raw",
        help=(
            "Advanced: FormID written to EDID. raw keeps the SST value, local keeps "
            "the lower 24 bits, light keeps lower 12 bits for ESL/light records."
        ),
    )
    parser.add_argument(
        "--no-sid",
        action="store_true",
        help="Do not write sID attributes from the SST 32-bit string IDs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    inputs: list[pathlib.Path] = args.input
    if len(inputs) > 1 and args.addon:
        print("error: --addon can only be used with one input file", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    if len(inputs) > 1 and args.output:
        output_dir = args.output

    failed = 0
    cli_source = args.source
    cli_dest = args.dest
    if args.lang_pair:
        try:
            cli_source, cli_dest = parse_language_pair(args.lang_pair)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    for input_path in inputs:
        if len(inputs) == 1 and args.output and not args.output_dir:
            output_path = args.output
        else:
            output_path = default_output_path(input_path, output_dir)

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            count, addon = convert_file(
                input_path=input_path,
                output_path=output_path,
                addon=args.addon,
                source_lang=cli_source,
                dest_lang=cli_dest,
                formid_mode=args.formid_mode,
                include_sid=not args.no_sid,
            )
        except (OSError, SstParseError) as exc:
            failed += 1
            print(f"error: {input_path}: {exc}", file=sys.stderr)
            continue

        print(f"wrote {count} entries: {output_path}")
        print(f"  addon: {addon}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
