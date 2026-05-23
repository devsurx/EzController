@echo off
color 0A
echo [*] IGNITION SEQUENCE STARTED...
echo.

:: Start the Python Server in a new window
start "Xbox Server" cmd /k "python server.py"

:: Start the secure ngrok tunnel in another new window
start "Secure Tunnel" cmd /k "ngrok http 5000"

exit