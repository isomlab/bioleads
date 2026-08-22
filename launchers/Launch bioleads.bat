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

REM --- keep this copy current -------------------------------------------------
REM Best-effort throughout: an offline PC, or a clone with local edits, still
REM launches on the code it already has.
set DOPULL=1
set NEEDS_ENV=
where git >nul 2>nul || set DOPULL=
if defined DOPULL git -C "%REPO%" rev-parse --is-inside-work-tree >nul 2>nul || set DOPULL=
if defined DOPULL git -C "%REPO%" remote get-url origin >nul 2>nul || set DOPULL=
if defined DOPULL git -C "%REPO%" symbolic-ref -q HEAD >nul 2>nul || set DOPULL=
if defined DOPULL for /f %%S in ('git -C "%REPO%" status --porcelain 2^>nul ^| find /c /v ""') do if not "%%S"=="0" set DOPULL=
if defined DOPULL echo Checking for updates...
if defined DOPULL for /f %%H in ('git -C "%REPO%" rev-parse HEAD 2^>nul') do set BEFORE=%%H
if defined DOPULL git -C "%REPO%" pull --ff-only --quiet 2>nul || echo   could not reach the server - launching the copy you have.
if defined DOPULL for /f %%H in ('git -C "%REPO%" rev-parse HEAD 2^>nul') do set AFTER=%%H
REM A new dependency is the only thing an editable install cannot pick up on its
REM own, so refresh the environment exactly when that pull touched the file.
if defined DOPULL if not "%BEFORE%"=="%AFTER%" for /f %%F in ('git -C "%REPO%" diff --name-only %BEFORE% %AFTER% 2^>nul ^| findstr /x "environment.yml"') do set NEEDS_ENV=1
if defined NEEDS_ENV echo Dependencies changed - updating the %ENV_NAME% environment...
if defined NEEDS_ENV pushd "%REPO%"
if defined NEEDS_ENV %CONDA% env update -f environment.yml
if defined NEEDS_ENV popd

echo Starting bioleads...
set PYTHONPATH=
cd /d "%USERPROFILE%"
%CONDA% run --no-capture-output -n %ENV_NAME% bioleads-gui
