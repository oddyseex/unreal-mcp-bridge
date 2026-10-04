@echo off
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment and installing dependencies...
  py -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
echo Starting Unreal MCP server... press Ctrl+C to stop.
python server\unreal_mcp_server.py
