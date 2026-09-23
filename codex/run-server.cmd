@echo off
setlocal

set "PYTHON_EXE="
set "CONDA_EXE_PATH=%CONDA_EXE%"

rem If CODEX_CONDA_ENV is a named environment, let conda select its Python.
rem A full environment path is handled directly below.
if defined CODEX_CONDA_ENV if exist "%CODEX_CONDA_ENV%\python.exe" set "PYTHON_EXE=%CODEX_CONDA_ENV%\python.exe"
if not defined CONDA_EXE_PATH for /f "delims=" %%C in ('where conda.exe 2^>nul') do if not defined CONDA_EXE_PATH set "CONDA_EXE_PATH=%%C"
if defined CODEX_CONDA_ENV if not defined PYTHON_EXE if defined CONDA_EXE_PATH (
  "%CONDA_EXE_PATH%" run --no-capture-output -n "%CODEX_CONDA_ENV%" python "%~dp0server.py" %*
  exit /b %ERRORLEVEL%
)

rem When Codex is launched from an activated environment, use that Python.
if not defined PYTHON_EXE if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "PYTHON_EXE=%CONDA_PREFIX%\python.exe"
if defined CODEX_PYTHON if exist "%CODEX_PYTHON%" set "PYTHON_EXE=%CODEX_PYTHON%"
if not defined PYTHON_EXE if defined USERPROFILE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "PYTHON_EXE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not defined PYTHON_EXE if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\OpenAI\Codex\python\python.exe" set "PYTHON_EXE=%LOCALAPPDATA%\OpenAI\Codex\python\python.exe"

if not defined PYTHON_EXE for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"

if not defined PYTHON_EXE (
  >&2 echo ssh: Python 3 was not found. Set CODEX_PYTHON or CODEX_CONDA_ENV.
  exit /b 127
)

"%PYTHON_EXE%" "%~dp0server.py" %*
exit /b %ERRORLEVEL%
