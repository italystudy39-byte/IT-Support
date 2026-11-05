@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
if not exist .venv (
  echo Устанавливаю зависимости, это займёт 1-2 минуты...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
.venv\Scripts\python -c "import google.genai" 2>nul || .venv\Scripts\python -m pip install -q -r requirements.txt
findstr /C:"TELEGRAM_BOT_TOKEN=123456:ABC" .env >nul && (
  echo Откройте файл .env и впишите TELEGRAM_BOT_TOKEN и GEMINI_API_KEY ^(или ANTHROPIC_API_KEY^), затем запустите run.bat ещё раз.
  notepad .env
  pause
  exit /b
)
echo Бот запущен. Не закрывайте это окно. Ctrl+C - остановить.
.venv\Scripts\python -m helpdesk.channels.telegram_bot
pause
