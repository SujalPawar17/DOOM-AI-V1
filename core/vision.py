import cv2
import numpy as np
import pyautogui
import PIL.Image
import os
from typing import List, Tuple, Dict, Any
import json
from datetime import datetime

# Try to import optional face recognition
try:
    import face_recognition
    FACE_RECOGNITION_AVAILABLE = True
except ImportError:
    FACE_RECOGNITION_AVAILABLE = False

class DOOMVision:
    def __init__(self):
        self.known_faces = {}
        self.face_encodings = []
        self.face_names = []
        if FACE_RECOGNITION_AVAILABLE:
            self.load_known_faces()
        
    def load_known_faces(self):
        """Load Sujal's reference face"""
        if not FACE_RECOGNITION_AVAILABLE:
            return
        try:
            if os.path.exists("sujal.jpg"):
                sujal_image = face_recognition.load_image_file("sujal.jpg")
                sujal_encoding = face_recognition.face_encodings(sujal_image)[0]
                self.known_faces["sujal"] = sujal_encoding
                self.face_encodings.append(sujal_encoding)
                self.face_names.append("Sujal")
        except Exception as e:
            print(f"[NOTE] Face recognition loading: {e}")
    
    def capture_screen(self) -> np.ndarray:
        """Capture current screen"""
        try:
            screenshot = pyautogui.screenshot()
            return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    
    def analyze_screen(self) -> Dict[str, Any]:
        """Analyze screen luminance and dominant colors"""
        try:
            screen = self.capture_screen()
            if screen is None:
                return {"error": "Failed to capture screen"}
            height, width = screen.shape[:2]
            brightness = float(np.mean(screen))
            
            face_count = 0
            if FACE_RECOGNITION_AVAILABLE:
                try:
                    face_locations = face_recognition.face_locations(screen)
                    face_count = len(face_locations)
                except Exception:
                    face_count = 0

            return {
                "resolution": f"{width}x{height}",
                "brightness": f"{brightness:.1f}",
                "face_count": face_count,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
        except Exception as e:
            return {"error": str(e)}

    def detect_hand_gesture(self, frame: np.ndarray) -> str:
        """Detect hand gestures (Palm, Fist, Peace Sign) via OpenCV contour & convex hull analysis"""
        try:
            # Convert to HSV color space for skin tone segmentation
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            lower_skin = np.array([0, 20, 70], dtype=np.uint8)
            upper_skin = np.array([20, 255, 255], dtype=np.uint8)
            mask = cv2.inRange(hsv, lower_skin, upper_skin)
            
            # Apply blur and morphological operations
            blur = cv2.GaussianBlur(mask, (5, 5), 0)
            contours, _ = cv2.findContours(blur, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            if not contours:
                return "No hand detected"
                
            max_contour = max(contours, key=cv2.contourArea)
            if cv2.contourArea(max_contour) < 5000:
                return "No clear hand gesture"
                
            hull = cv2.convexHull(max_contour, returnPoints=False)
            if len(hull) > 3:
                defects = cv2.convexityDefects(max_contour, hull)
                if defects is not None:
                    finger_count = 0
                    for i in range(defects.shape[0]):
                        s, e, f, d = defects[i, 0]
                        start = tuple(max_contour[s][0])
                        end = tuple(max_contour[e][0])
                        far = tuple(max_contour[f][0])
                        
                        a = np.sqrt((end[0] - start[0])**2 + (end[1] - start[1])**2)
                        b = np.sqrt((far[0] - start[0])**2 + (far[1] - start[1])**2)
                        c = np.sqrt((end[0] - far[0])**2 + (end[1] - far[1])**2)
                        
                        angle = np.arccos(max(-1.0, min(1.0, (b**2 + c**2 - a**2) / (2 * b * c + 1e-5))))
                        if angle <= np.pi / 2 and d > 10000:
                            finger_count += 1
                    
                    if finger_count >= 4:
                        return "Open Palm (Stop / Mute)"
                    elif finger_count == 2:
                        return "Peace / Victory Sign (Activate)"
                    elif finger_count == 1:
                        return "Pointing / 1 Finger"
                    else:
                        return "Fist / Closed Hand"
            return "Hand Present"
        except Exception:
            return "Detection error"

    def scan_webcam_gesture(self, duration: int = 5) -> str:
        """Scan webcam for active gestures"""
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return "Webcam not available"
            start_time = datetime.now()
            detected_gestures = []
            while (datetime.now() - start_time).seconds < duration:
                ret, frame = cap.read()
                if ret:
                    gesture = self.detect_hand_gesture(frame)
                    if "No" not in gesture and "error" not in gesture:
                        detected_gestures.append(gesture)
                cv2.waitKey(50)
            cap.release()
            
            if detected_gestures:
                # Return most frequent gesture
                return max(set(detected_gestures), key=detected_gestures.count)
            return "No clear gesture recognized"
        except Exception as e:
            return f"Gesture scan error: {e}"

    def take_photo(self, filename: str = None) -> str:
        """Take a photo using webcam"""
        try:
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return "Could not access webcam, Sujal."
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return "Could not capture webcam frame."
            if filename is None:
                filename = f"doom_photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(filename, frame)
            return f"Photo captured and saved as {filename}, Sujal."
        except Exception as e:
            return f"Photo capture error: {e}"

# Global vision instance
vision = DOOMVision()