@echo off
REM bioleads - double-click launcher (Windows).
REM First run creates the 'bioleads' conda environment; later runs just open the app.
REM Requirement: install Miniforge once: https://conda-forge.org/download/
setlocal
set ENV_NAME=bioleads
set HERE=%~dp0
set REPO=%HERE%..

set CONDA=
where conda >nul 2>nul && set CONDA=conda
if "%CONDA%"=="" (
  for %%C in ("%USERPROFILE%\miniforge3" "%USERPROFILE%\mambaforge" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3") do (
    if exist "%%~C\Scripts\conda.exe" set CONDA="%%~C\Scripts\conda.exe"
  )
)
if "%CONDA%"=="" (
  echo Could not find conda on this PC.
  echo Please install Miniforge first: https://conda-forge.org/download/
  pause
  exit /b 1
)

%CONDA% env list | findstr /b /c:"%ENV_NAME% " >nul
if errorlevel 1 (
  echo First-time setup: creating the %ENV_NAME% environment...
  pushd "%REPO%"
  %CONDA% env create -f environment.yml
  set CREATE_ERR=%errorlevel%
  popd
  if not "%CREATE_ERR%"=="0" ( echo Setup did not finish. & pause & exit /b 1 )
)

echo Starting bioleads...
set PYTHONPATH=
cd /d "%USERPROFILE%"
%CONDA% run --no-capture-output -n %ENV_NAME% bioleads-gui
