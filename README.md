# SST to FallHook XML

Converts an xTranslator `.sst` dictionary into FallHook-compatible XML.

The output keeps the xTranslator XML structure, but writes bracket FormIDs in
the `EDID` element:

```xml
<EDID>[0000175C]</EDID>
```

FallHook already parses this bracket form through `ParseBracketFormID`.

SST strings are decoded as UTF-16LE. The converter does not prefer a local
Windows code page such as CP949, so the same build should work for Korean,
Japanese, Chinese, European languages, and mixed-language dictionaries as long
as the SST follows the xTranslator SSU8 or SSU9 string layout.

The GUI asks for the XML `Params` language pair directly. Values such as
`en_ko`, `en_ja`, `en_ru`, or `fr_de` are accepted. This is not hardcoded to
Korean; any 2- or 3-letter pair separated by `_` or `-` is accepted.

## Usage

GUI:

```bat
run_gui.bat
```

The GUI is intended for batch conversion. Add one or more `.sst` files, choose
an output folder, enter a language pair such as `en_ko`, then convert all of
them at once.

CLI:

```bat
convert_sst_to_fallhook_xml.bat input.sst more.sst
```

or:

```bat
python sst_to_fallhook_xml.py input.sst more.sst --output-dir output_folder
```

To set the language pair explicitly:

```bat
python sst_to_fallhook_xml.py input.sst more.sst --output-dir output_folder --lang-pair en_ru
```

For one input file, `-o output.xml` writes to a specific XML file. For multiple
input files, `-o output_folder` is treated as an output directory.

By default, the tool writes the raw SST FormID. FallHook resolves that value
against the detected plugin and trims it as needed for normal or light plugins.
The CLI still exposes an advanced override for troubleshooting:

```bat
python sst_to_fallhook_xml.py input.sst -o output.xml --formid-mode local
```

## Building a Windows EXE

```bat
python -m PyInstaller --noconfirm --onefile --windowed --name SST2FallHookXML sst_to_fallhook_xml_gui.py
```

The built executable will be under `dist\SST2FallHookXML.exe`.
