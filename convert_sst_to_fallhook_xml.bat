@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   %~nx0 input.sst [more.sst ...]
  echo.
  echo Example:
  echo   %~nx0 csepraidercompanion_en_ko.sst another_dictionary.sst
  exit /b 2
)

python "%~dp0sst_to_fallhook_xml.py" %*
