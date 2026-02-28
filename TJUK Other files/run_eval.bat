@echo off
echo Installing dependencies...
pip install anthropic openai google-generativeai requests openpyxl pandas
echo.
echo Running eval...
python eval_harness.py
echo.
echo Done! Check WhatsApp_Eval_Results.xlsx
pause
