@echo off
:: O /d muda para a unidade e pasta do script, o cd /d garante o diretório correto
cd /d "%~dp0"
:: wt -w 0: força usar a janela atual. -d "%~dp0": garante o diretório de início correto.
start wt -w 0 -d "." powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0run_yatra.ps1"
exit