@echo off
cd /d "%~dp0"
if "%SP_PDF_DIR%"=="" set "SP_PDF_DIR=%~dp0incoming_sp_pdfs"
python -m streamlit run "%~dp0sp_app.py" --server.port 8501 --server.headless true
