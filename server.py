import asyncio
import websockets
import json
import vgamepad as vg
from websockets.http11 import Response
from websockets.datastructures import Headers

class SteeringServer:
    def __init__(self):
        print("[*] Ultimate Xbox Server Initialized.")
        self.gamepad = vg.VX360Gamepad()
        
    async def handler(self, websocket):
        print("\n[+] 🟢 WEBSOCKET CONNECTED! The rig is fully online!")
        try:
            async for message in websocket:
                data = json.loads(message)
                
                # --- JOYSTICKS & TRIGGERS ---
                # Left Joystick (Gyro Steering)
                steer = data.get('steering', 0.0)
                self.gamepad.left_joystick_float(x_value_float=steer, y_value_float=0.0)
                
                # Right Joystick (Touch Camera Control)
                rs_x = data.get('rs_x', 0.0)
                rs_y = data.get('rs_y', 0.0)
                self.gamepad.right_joystick_float(x_value_float=float(rs_x), y_value_float=float(rs_y))
                
                # Triggers (Gas & Brake)
                throttle = data.get('throttle', 0)
                self.gamepad.right_trigger_float(value_float=float(throttle))
                brake = data.get('brake', 0)
                self.gamepad.left_trigger_float(value_float=float(brake))
                
                # --- ALL BUTTONS DICTIONARY ---
                button_map = {
                    'handbrake': vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
                    'y_btn': vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
                    'view_btn': vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
                    'menu_btn': vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
                    'guide_btn': vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
                    'dpad_up': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
                    'dpad_down': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
                    'dpad_left': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
                    'dpad_right': vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
                }
                
                # Press or release every button based on the phone's data
                for key, btn in button_map.items():
                    if data.get(key, 0):
                        self.gamepad.press_button(button=btn)
                    else:
                        self.gamepad.release_button(button=btn)
                
                self.gamepad.update()
                
        except websockets.exceptions.ConnectionClosed:
            print("[-] 🔴 Phone disconnected. Applying emergency brakes.")
            self.gamepad.reset()
            self.gamepad.update()

def serve_html(connection, request):
    clean_path = request.path.split('?')[0]
    if clean_path == "/ws":
        return None
    if clean_path == "/":
        try:
            with open("index.html", "rb") as f:
                body = f.read()
            headers = Headers({
                "Content-Type": "text/html",
                "Content-Length": str(len(body))
            })
            return Response(200, "OK", headers, body)
        except Exception as e:
            print(f"Error reading index.html: {e}")
    return Response(404, "Not Found", Headers(), b"")

async def main():
    server = SteeringServer()
    print("[*] Server running. Waiting for connection on port 5000...")
    async with websockets.serve(server.handler, "0.0.0.0", 5000, process_request=serve_html):
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Server shutting down.")