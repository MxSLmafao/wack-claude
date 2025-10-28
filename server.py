"""
Real-Time Gesture & Expression Recognition Server
Handles all MediaPipe processing and detection logic
"""

import os
import base64
import cv2
import numpy as np
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import mediapipe as mp
from dataclasses import dataclass
from typing import Optional, Dict, Any
import json

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = 'gesture-recognition-secret-key'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize MediaPipe
mp_hands = mp.solutions.hands
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

# Global MediaPipe instances
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    model_complexity=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)

face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)

# Gesture and Expression Definitions
@dataclass
class Detection:
    emoji: str
    label: str
    confidence: float
    type: str  # 'hand' or 'face'

GESTURES = {
    'AIRPLANE': {'emoji': '✈️', 'label': 'Airplane Gesture'},
    'THUMBS_UP': {'emoji': '👍', 'label': 'Thumbs Up'},
    'PEACE': {'emoji': '✌️', 'label': 'Peace Sign'},
    'FIST': {'emoji': '👊', 'label': 'Fist'},
    'OPEN_PALM': {'emoji': '🖐️', 'label': 'Open Palm'},
    'LOVE_YOU': {'emoji': '🤟', 'label': 'Love You Sign'}
}

EXPRESSIONS = {
    'TONGUE_OUT': {'emoji': '😛', 'label': 'Tongue Out'},
    'SMILE': {'emoji': '😊', 'label': 'Smiling'},
    'SURPRISED': {'emoji': '😮', 'label': 'Surprised'},
    'ANGRY': {'emoji': '😠', 'label': 'Angry'},
    'WINK': {'emoji': '😉', 'label': 'Winking'}
}


class GestureDetector:
    """Hand gesture detection algorithms"""

    @staticmethod
    def distance(p1, p2) -> float:
        """Calculate Euclidean distance between two points"""
        return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

    @staticmethod
    def is_finger_extended(tip, pip) -> bool:
        """Check if a finger is extended"""
        return tip.y < pip.y - 0.02

    @staticmethod
    def detect(landmarks, handedness) -> Optional[Detection]:
        """Detect hand gesture from landmarks"""
        if not landmarks:
            return None

        # Extract key landmarks
        thumb_tip = landmarks[4]
        thumb_ip = landmarks[3]
        index_tip = landmarks[8]
        index_pip = landmarks[6]
        middle_tip = landmarks[12]
        middle_pip = landmarks[10]
        ring_tip = landmarks[16]
        ring_pip = landmarks[14]
        pinky_tip = landmarks[20]
        pinky_pip = landmarks[18]
        wrist = landmarks[0]

        # Check finger states
        finger_states = {
            'thumb': thumb_tip.x < thumb_ip.x - 0.02 or thumb_tip.x > thumb_ip.x + 0.02,
            'index': GestureDetector.is_finger_extended(index_tip, index_pip),
            'middle': GestureDetector.is_finger_extended(middle_tip, middle_pip),
            'ring': GestureDetector.is_finger_extended(ring_tip, ring_pip),
            'pinky': GestureDetector.is_finger_extended(pinky_tip, pinky_pip)
        }

        # THUMBS UP: Only thumb extended, others down
        if (finger_states['thumb'] and thumb_tip.y < wrist.y and
            not finger_states['index'] and not finger_states['middle'] and
            not finger_states['ring'] and not finger_states['pinky']):
            return Detection(
                emoji=GESTURES['THUMBS_UP']['emoji'],
                label=GESTURES['THUMBS_UP']['label'],
                confidence=0.95,
                type='hand'
            )

        # PEACE SIGN: Index and middle extended, others down
        if (finger_states['index'] and finger_states['middle'] and
            not finger_states['ring'] and not finger_states['pinky']):
            return Detection(
                emoji=GESTURES['PEACE']['emoji'],
                label=GESTURES['PEACE']['label'],
                confidence=0.92,
                type='hand'
            )

        # LOVE YOU SIGN: Thumb, index, and pinky extended
        if (finger_states['thumb'] and finger_states['index'] and finger_states['pinky'] and
            not finger_states['middle'] and not finger_states['ring']):
            return Detection(
                emoji=GESTURES['LOVE_YOU']['emoji'],
                label=GESTURES['LOVE_YOU']['label'],
                confidence=0.90,
                type='hand'
            )

        # OPEN PALM: All fingers extended and spread
        if (finger_states['index'] and finger_states['middle'] and
            finger_states['ring'] and finger_states['pinky']):
            spread = GestureDetector.distance(index_tip, pinky_tip)
            if spread > 0.15:
                # AIRPLANE: More spread with thumb
                if spread > 0.2 and finger_states['thumb']:
                    return Detection(
                        emoji=GESTURES['AIRPLANE']['emoji'],
                        label=GESTURES['AIRPLANE']['label'],
                        confidence=0.85,
                        type='hand'
                    )
                return Detection(
                    emoji=GESTURES['OPEN_PALM']['emoji'],
                    label=GESTURES['OPEN_PALM']['label'],
                    confidence=0.88,
                    type='hand'
                )

        # FIST: All fingers down
        if (not finger_states['index'] and not finger_states['middle'] and
            not finger_states['ring'] and not finger_states['pinky']):
            return Detection(
                emoji=GESTURES['FIST']['emoji'],
                label=GESTURES['FIST']['label'],
                confidence=0.90,
                type='hand'
            )

        return None


class ExpressionDetector:
    """Facial expression detection algorithms"""

    @staticmethod
    def distance(p1, p2) -> float:
        """Calculate Euclidean distance between two points"""
        return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

    @staticmethod
    def detect(landmarks) -> Optional[Detection]:
        """Detect facial expression from landmarks"""
        if not landmarks:
            return None

        # Key facial landmarks
        left_eye = landmarks[33]
        right_eye = landmarks[263]
        left_eye_top = landmarks[159]
        left_eye_bottom = landmarks[145]
        right_eye_top = landmarks[386]
        right_eye_bottom = landmarks[374]
        left_mouth = landmarks[61]
        right_mouth = landmarks[291]
        top_lip = landmarks[13]
        bottom_lip = landmarks[14]
        left_eyebrow = landmarks[70]
        right_eyebrow = landmarks[300]
        nose_tip = landmarks[1]

        # Calculate metrics
        left_eye_height = ExpressionDetector.distance(left_eye_top, left_eye_bottom)
        right_eye_height = ExpressionDetector.distance(right_eye_top, right_eye_bottom)
        mouth_width = ExpressionDetector.distance(left_mouth, right_mouth)
        mouth_height = ExpressionDetector.distance(top_lip, bottom_lip)
        mouth_aspect_ratio = mouth_height / mouth_width if mouth_width > 0 else 0

        # WINKING: One eye significantly smaller than the other
        eye_ratio = abs(left_eye_height - right_eye_height) / max(left_eye_height, right_eye_height)
        if eye_ratio > 0.3:
            return Detection(
                emoji=EXPRESSIONS['WINK']['emoji'],
                label=EXPRESSIONS['WINK']['label'],
                confidence=0.88,
                type='face'
            )

        # SURPRISED: Large mouth opening and wide eyes
        if mouth_aspect_ratio > 0.35 and left_eye_height > 0.015 and right_eye_height > 0.015:
            return Detection(
                emoji=EXPRESSIONS['SURPRISED']['emoji'],
                label=EXPRESSIONS['SURPRISED']['label'],
                confidence=0.90,
                type='face'
            )

        # TONGUE OUT: Large mouth opening with specific shape
        if mouth_aspect_ratio > 0.3 and bottom_lip.y > top_lip.y + 0.02:
            return Detection(
                emoji=EXPRESSIONS['TONGUE_OUT']['emoji'],
                label=EXPRESSIONS['TONGUE_OUT']['label'],
                confidence=0.85,
                type='face'
            )

        # SMILE: Wide mouth, upturned corners
        if mouth_width > 0.12 and mouth_aspect_ratio < 0.25:
            if left_mouth.y < nose_tip.y and right_mouth.y < nose_tip.y:
                return Detection(
                    emoji=EXPRESSIONS['SMILE']['emoji'],
                    label=EXPRESSIONS['SMILE']['label'],
                    confidence=0.87,
                    type='face'
                )

        # ANGRY: Eyebrows lowered, mouth compressed
        eyebrows_lowered = (left_eyebrow.y > left_eye.y - 0.02 and
                           right_eyebrow.y > right_eye.y - 0.02)
        if eyebrows_lowered and mouth_aspect_ratio < 0.15:
            return Detection(
                emoji=EXPRESSIONS['ANGRY']['emoji'],
                label=EXPRESSIONS['ANGRY']['label'],
                confidence=0.82,
                type='face'
            )

        return None


def process_frame(image_data: str) -> Dict[str, Any]:
    """
    Process a single frame from the client
    Returns detection results and annotated frame
    """
    try:
        # Decode base64 image
        image_bytes = base64.b64decode(image_data.split(',')[1])
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if frame is None:
            return {'error': 'Failed to decode image'}

        # Convert BGR to RGB
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Process with MediaPipe
        hand_results = hands.process(rgb_frame)
        face_results = face_mesh.process(rgb_frame)

        # Detect gestures and expressions
        detections = []

        # Check hand gestures
        if hand_results.multi_hand_landmarks:
            for hand_idx, hand_landmarks in enumerate(hand_results.multi_hand_landmarks):
                handedness = hand_results.multi_handedness[hand_idx] if hand_results.multi_handedness else None
                detection = GestureDetector.detect(hand_landmarks.landmark, handedness)
                if detection:
                    detections.append(detection)

        # Check facial expressions
        if face_results.multi_face_landmarks:
            for face_landmarks in face_results.multi_face_landmarks:
                detection = ExpressionDetector.detect(face_landmarks.landmark)
                if detection:
                    detections.append(detection)

        # Get best detection (highest confidence)
        best_detection = None
        if detections:
            best_detection = max(detections, key=lambda d: d.confidence)

        # Prepare response
        response = {
            'success': True,
            'detection': None,
            'has_hands': hand_results.multi_hand_landmarks is not None,
            'has_face': face_results.multi_face_landmarks is not None
        }

        if best_detection:
            response['detection'] = {
                'emoji': best_detection.emoji,
                'label': best_detection.label,
                'confidence': best_detection.confidence,
                'type': best_detection.type
            }

        return response

    except Exception as e:
        print(f"Error processing frame: {str(e)}")
        return {'error': str(e), 'success': False}


# WebSocket Events
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f'Client connected: {request.sid}')
    emit('status', {'message': 'Connected to server', 'status': 'connected'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print(f'Client disconnected: {request.sid}')


@socketio.on('frame')
def handle_frame(data):
    """Handle incoming video frame from client"""
    try:
        image_data = data.get('image')
        if not image_data:
            emit('error', {'message': 'No image data received'})
            return

        # Process the frame
        result = process_frame(image_data)

        # Send result back to client
        emit('detection', result)

    except Exception as e:
        print(f"Error handling frame: {str(e)}")
        emit('error', {'message': str(e)})


@socketio.on('ping')
def handle_ping():
    """Handle ping from client for connection testing"""
    emit('pong', {'timestamp': request.sid})


# HTTP Routes
@app.route('/')
def index():
    """Serve the main HTML page"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Gesture Recognition - Server Running</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #0f172a;
                color: #f1f5f9;
            }
            h1 { color: #3b82f6; }
            .status {
                padding: 15px;
                background: #1e293b;
                border-radius: 8px;
                margin: 20px 0;
            }
            code {
                background: #334155;
                padding: 2px 6px;
                border-radius: 4px;
            }
            ul { line-height: 1.8; }
        </style>
    </head>
    <body>
        <h1>🎭 Gesture Recognition Server</h1>
        <div class="status">
            <h2>✅ Server is running!</h2>
            <p>MediaPipe models loaded and ready for processing.</p>
        </div>
        <h3>Available Endpoints:</h3>
        <ul>
            <li><strong>WebSocket:</strong> <code>ws://localhost:4564</code></li>
            <li><strong>HTTP:</strong> <code>http://localhost:4564</code></li>
        </ul>
        <h3>Next Steps:</h3>
        <ol>
            <li>Open <code>client.html</code> in your browser</li>
            <li>Click "Start Detection"</li>
            <li>Make gestures and expressions!</li>
        </ol>
        <h3>Detection Capabilities:</h3>
        <ul>
            <li>✈️ Airplane, 👍 Thumbs Up, ✌️ Peace, 👊 Fist, 🖐️ Open Palm, 🤟 Love You</li>
            <li>😛 Tongue Out, 😊 Smile, 😮 Surprised, 😠 Angry, 😉 Wink</li>
        </ul>
    </body>
    </html>
    '''


@app.route('/health')
def health():
    """Health check endpoint"""
    return {
        'status': 'healthy',
        'mediapipe': 'loaded',
        'models': ['hands', 'face_mesh']
    }


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Starting Gesture Recognition Server")
    print("=" * 60)
    print("Server: http://localhost:4564")
    print("WebSocket: ws://localhost:4564")
    print("Models: MediaPipe Hands + Face Mesh")
    print("=" * 60)
    print("\nPress Ctrl+C to stop the server\n")

    socketio.run(app, host='0.0.0.0', port=4564, debug=True, allow_unsafe_werkzeug=True)
