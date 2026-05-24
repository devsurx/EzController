# 🏎️ Wireless Gyro Racing Wheel (Virtual Xbox Controller)

Turn your smartphone into a fully functional, wireless PC steering wheel and Xbox 360 controller. No apps to install on your phone—just a lightweight Python server and a web browser.

Built for racing games (Forza, Assetto Corsa, Need for Speed), this project uses your phone's internal gyroscope for hyper-sensitive steering and maps your touch inputs to a virtual Xbox 360 gamepad on your Windows PC.

## ✨ Features

* **Zero-Install Client:** Runs entirely in your mobile browser (Safari/Chrome).
* **Wireless Gyro Steering:** Tilt your phone to steer. The sensitivity is cranked up (25° lock-to-lock) for snappy, aggressive drifting.
* **Authentic Xbox UI:** A sleek, dark-mode replica of an Xbox controller right on your screen.
* **Full Mapping:** Gas/Brake triggers, ABXY face buttons, D-Pad, Start/Select, and an interactive right thumbstick for camera control.
* **1-Click Launch:** Boot up the server and secure tunnel instantly with a Windows batch script.

---

## 🛠️ Prerequisites

* A Windows PC with Python installed.
* The required Python libraries (`websockets` and `vgamepad`) installed.
* **ngrok** installed on your system. *(Note: Modern mobile browsers strictly disable the gyroscope on local networks for security reasons. We use ngrok to create a secure tunnel to bypass this block).*

---

## 🚀 How to Run

1. Double-click the **Start Racing.bat** file on your PC.
2. This will automatically launch the server and the secure tunnel in two separate windows.
3. Look at the ngrok window and copy the secure link (it will look like `https://...ngrok-free.app`).
4. Send this link to your phone and open it in your mobile browser.

---

## 🎮 How to Play

1. Make sure your phone is connected to **Wi-Fi or Cellular** (you don't need a USB cable!).
2. Open the secure ngrok link on your phone.
3. Tap **IGNITION**.
4. *Important:* If your phone prompts you for "Device Motion / Gyroscope" permissions, hit **Allow**.
5. The Xbox Guide button will light up green, and the PC terminal will confirm the connection is online.
6. Boot up your favorite racing game. Windows will recognize your phone as a standard Xbox 360 Controller. Hit the gas and drive!

---

## 📂 Project Structure

* **server.py**: The Python backend. It serves the interface, handles the connection, and translates the data into virtual Xbox 360 inputs.
* **index.html**: The frontend UI. Handles the device orientation API (gyroscope), multi-touch inputs, the interactive UI, and sends data to the server at ~60Hz.
* **Start Racing.bat**: A simple Windows macro to start both the Python server and the network tunnel simultaneously.