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
# Using static_image_mode=True because we process independent frames from WebSocket
hands = mp_hands.Hands(
    static_image_mode=True,  # True for independent frame processing
    max_num_hands=2,
    model_complexity=1,
    min_detection_confidence=0.5,  # Lowered for better detection
    min_tracking_confidence=0.5     # Not used in static mode, but kept for consistency
)

face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=True,  # True for independent frame processing
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,  # Lowered for better detection
    min_tracking_confidence=0.5     # Not used in static mode, but kept for consistency
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
    """Hand gesture detection algorithms - Improved version"""

    @staticmethod
    def distance(p1, p2) -> float:
        """Calculate Euclidean distance between two points"""
        return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

    @staticmethod
    def is_finger_extended(tip, pip, mcp) -> bool:
        """Check if a finger is extended using multiple joints"""
        # Finger is extended if tip is higher than pip and pip is higher than mcp
        return tip.y < pip.y and pip.y < mcp.y

    @staticmethod
    def is_thumb_extended(thumb_tip, thumb_ip, thumb_mcp, index_mcp, is_right_hand) -> bool:
        """Check if thumb is extended considering handedness"""
        # Distance between thumb tip and index base
        dist_to_index = GestureDetector.distance(thumb_tip, index_mcp)

        # For right hand, thumb extends to the left; for left hand, to the right
        if is_right_hand:
            horizontal_extended = thumb_tip.x < thumb_ip.x
        else:
            horizontal_extended = thumb_tip.x > thumb_ip.x

        return dist_to_index > 0.1 or horizontal_extended

    @staticmethod
    def detect(landmarks, handedness) -> Optional[Detection]:
        """Detect hand gesture from landmarks with improved accuracy"""
        if not landmarks:
            return None

        # Determine if it's a right hand
        is_right_hand = True
        if handedness and hasattr(handedness, 'classification'):
            is_right_hand = handedness.classification[0].label == 'Right'

        # Extract all key landmarks
        wrist = landmarks[0]

        thumb_cmc = landmarks[1]
        thumb_mcp = landmarks[2]
        thumb_ip = landmarks[3]
        thumb_tip = landmarks[4]

        index_mcp = landmarks[5]
        index_pip = landmarks[6]
        index_dip = landmarks[7]
        index_tip = landmarks[8]

        middle_mcp = landmarks[9]
        middle_pip = landmarks[10]
        middle_dip = landmarks[11]
        middle_tip = landmarks[12]

        ring_mcp = landmarks[13]
        ring_pip = landmarks[14]
        ring_dip = landmarks[15]
        ring_tip = landmarks[16]

        pinky_mcp = landmarks[17]
        pinky_pip = landmarks[18]
        pinky_dip = landmarks[19]
        pinky_tip = landmarks[20]

        # Improved finger state detection
        thumb_extended = GestureDetector.is_thumb_extended(thumb_tip, thumb_ip, thumb_mcp, index_mcp, is_right_hand)
        index_extended = GestureDetector.is_finger_extended(index_tip, index_pip, index_mcp)
        middle_extended = GestureDetector.is_finger_extended(middle_tip, middle_pip, middle_mcp)
        ring_extended = GestureDetector.is_finger_extended(ring_tip, ring_pip, ring_mcp)
        pinky_extended = GestureDetector.is_finger_extended(pinky_tip, pinky_pip, pinky_mcp)

        # Count extended fingers
        extended_count = sum([index_extended, middle_extended, ring_extended, pinky_extended])

        # FIST: All fingers curled (check first for priority)
        if not index_extended and not middle_extended and not ring_extended and not pinky_extended:
            # Verify fingers are actually curled toward palm
            fingertips_close = (
                GestureDetector.distance(index_tip, wrist) < 0.15 and
                GestureDetector.distance(middle_tip, wrist) < 0.15
            )
            if fingertips_close or extended_count == 0:
                return Detection(
                    emoji=GESTURES['FIST']['emoji'],
                    label=GESTURES['FIST']['label'],
                    confidence=0.93,
                    type='hand'
                )

        # THUMBS UP: Only thumb extended vertically, others curled
        if thumb_extended and extended_count == 0:
            # Thumb must be above wrist
            thumb_vertical = thumb_tip.y < wrist.y - 0.05
            if thumb_vertical:
                return Detection(
                    emoji=GESTURES['THUMBS_UP']['emoji'],
                    label=GESTURES['THUMBS_UP']['label'],
                    confidence=0.96,
                    type='hand'
                )

        # PEACE SIGN: Only index and middle extended
        if index_extended and middle_extended and not ring_extended and not pinky_extended:
            # Check that index and middle are separated (V shape)
            finger_spread = GestureDetector.distance(index_tip, middle_tip)
            if finger_spread > 0.03:
                return Detection(
                    emoji=GESTURES['PEACE']['emoji'],
                    label=GESTURES['PEACE']['label'],
                    confidence=0.94,
                    type='hand'
                )

        # LOVE YOU SIGN: Thumb, index, and pinky extended
        if thumb_extended and index_extended and pinky_extended and not middle_extended and not ring_extended:
            return Detection(
                emoji=GESTURES['LOVE_YOU']['emoji'],
                label=GESTURES['LOVE_YOU']['label'],
                confidence=0.92,
                type='hand'
            )

        # AIRPLANE: Thumb, middle, and pinky extended (index and ring folded)
        if thumb_extended and not index_extended and middle_extended and not ring_extended and pinky_extended:
            return Detection(
                emoji=GESTURES['AIRPLANE']['emoji'],
                label=GESTURES['AIRPLANE']['label'],
                confidence=0.91,
                type='hand'
            )

        # OPEN PALM: All four fingers extended
        if index_extended and middle_extended and ring_extended and pinky_extended:
            # Measure the span from index to pinky
            hand_span = GestureDetector.distance(index_tip, pinky_tip)

            # OPEN PALM: All fingers up
            if hand_span > 0.12:
                return Detection(
                    emoji=GESTURES['OPEN_PALM']['emoji'],
                    label=GESTURES['OPEN_PALM']['label'],
                    confidence=0.90,
                    type='hand'
                )

        return None


class ExpressionDetector:
    """Facial expression detection algorithms - Improved version"""

    @staticmethod
    def distance(p1, p2) -> float:
        """Calculate Euclidean distance between two points"""
        return np.sqrt((p1.x - p2.x)**2 + (p1.y - p2.y)**2 + (p1.z - p2.z)**2)

    @staticmethod
    def detect(landmarks) -> Optional[Detection]:
        """Detect facial expression from landmarks with improved accuracy"""
        if not landmarks:
            return None

        # Key facial landmarks (MediaPipe Face Mesh indices)
        # Eyes
        left_eye_inner = landmarks[133]
        left_eye_outer = landmarks[33]
        left_eye_top = landmarks[159]
        left_eye_bottom = landmarks[145]

        right_eye_inner = landmarks[362]
        right_eye_outer = landmarks[263]
        right_eye_top = landmarks[386]
        right_eye_bottom = landmarks[374]

        # Mouth
        left_mouth = landmarks[61]
        right_mouth = landmarks[291]
        top_lip_top = landmarks[13]
        top_lip_bottom = landmarks[14]
        bottom_lip_top = landmarks[13]
        bottom_lip_bottom = landmarks[14]
        mouth_center_top = landmarks[13]
        mouth_center_bottom = landmarks[14]

        # Eyebrows
        left_eyebrow_inner = landmarks[107]
        left_eyebrow_outer = landmarks[66]
        right_eyebrow_inner = landmarks[336]
        right_eyebrow_outer = landmarks[296]

        # Reference points
        nose_tip = landmarks[1]
        nose_bridge = landmarks[6]

        # Calculate comprehensive metrics
        left_eye_height = ExpressionDetector.distance(left_eye_top, left_eye_bottom)
        right_eye_height = ExpressionDetector.distance(right_eye_top, right_eye_bottom)
        left_eye_width = ExpressionDetector.distance(left_eye_inner, left_eye_outer)
        right_eye_width = ExpressionDetector.distance(right_eye_inner, right_eye_outer)

        mouth_width = ExpressionDetector.distance(left_mouth, right_mouth)
        mouth_height = ExpressionDetector.distance(mouth_center_top, mouth_center_bottom)
        mouth_aspect_ratio = mouth_height / mouth_width if mouth_width > 0 else 0

        # Average eye metrics for normalization
        avg_eye_height = (left_eye_height + right_eye_height) / 2
        avg_eye_width = (left_eye_width + right_eye_width) / 2

        # Eye aspect ratios
        left_ear = left_eye_height / left_eye_width if left_eye_width > 0 else 0
        right_ear = right_eye_height / right_eye_width if right_eye_width > 0 else 0

        # WINKING: One eye closed or significantly smaller
        eye_height_diff = abs(left_eye_height - right_eye_height)
        eye_ratio = eye_height_diff / max(left_eye_height, right_eye_height) if max(left_eye_height, right_eye_height) > 0 else 0

        # Improved wink detection - check if one eye is significantly smaller
        if eye_ratio > 0.4 or (left_ear < 0.15 and right_ear > 0.2) or (right_ear < 0.15 and left_ear > 0.2):
            return Detection(
                emoji=EXPRESSIONS['WINK']['emoji'],
                label=EXPRESSIONS['WINK']['label'],
                confidence=0.91,
                type='face'
            )

        # SURPRISED: Large mouth opening with wide eyes
        # Both mouth very open and eyes wide open
        if mouth_aspect_ratio > 0.4 and avg_eye_height > 0.012:
            # Additional check: mouth is notably wider than normal
            mouth_very_open = mouth_height > 0.04
            if mouth_very_open:
                return Detection(
                    emoji=EXPRESSIONS['SURPRISED']['emoji'],
                    label=EXPRESSIONS['SURPRISED']['label'],
                    confidence=0.93,
                    type='face'
                )

        # TONGUE OUT: Mouth open with specific characteristics
        # Larger mouth opening than smile, but not as wide as surprised
        if mouth_aspect_ratio > 0.25 and mouth_aspect_ratio < 0.5:
            # Check for vertical mouth opening (tongue out position)
            mouth_somewhat_open = mouth_height > 0.025
            if mouth_somewhat_open:
                return Detection(
                    emoji=EXPRESSIONS['TONGUE_OUT']['emoji'],
                    label=EXPRESSIONS['TONGUE_OUT']['label'],
                    confidence=0.87,
                    type='face'
                )

        # SMILE: Wide mouth with upturned corners, closed or slightly open
        # Mouth corners should be higher than center bottom
        mouth_corners_up = (left_mouth.y < mouth_center_bottom.y and
                           right_mouth.y < mouth_center_bottom.y)

        # Wide smile detection
        smile_width = mouth_width > 0.10
        smile_not_too_open = mouth_aspect_ratio < 0.3

        if smile_width and smile_not_too_open and mouth_corners_up:
            # Additional verification: corners above nose tip indicates strong smile
            strong_smile = left_mouth.y < nose_tip.y and right_mouth.y < nose_tip.y
            confidence = 0.92 if strong_smile else 0.86

            return Detection(
                emoji=EXPRESSIONS['SMILE']['emoji'],
                label=EXPRESSIONS['SMILE']['label'],
                confidence=confidence,
                type='face'
            )

        # ANGRY: Eyebrows lowered and furrowed, mouth tight or frowning
        # Check eyebrow positions relative to eyes
        left_brow_lowered = left_eyebrow_inner.y > left_eye_top.y - 0.015
        right_brow_lowered = right_eyebrow_inner.y > right_eye_top.y - 0.015

        # Mouth characteristics for anger
        mouth_tight = mouth_aspect_ratio < 0.12
        mouth_corners_down = (left_mouth.y > mouth_center_bottom.y - 0.01 and
                             right_mouth.y > mouth_center_bottom.y - 0.01)

        if (left_brow_lowered and right_brow_lowered) and (mouth_tight or mouth_corners_down):
            return Detection(
                emoji=EXPRESSIONS['ANGRY']['emoji'],
                label=EXPRESSIONS['ANGRY']['label'],
                confidence=0.85,
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
    """Serve the main client application"""
    try:
        with open('client.html', 'r', encoding='utf-8') as f:
            content = f.read()
        return content, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except FileNotFoundError:
        return '''
        <html>
        <body style="font-family: Arial; padding: 50px; background: #0f172a; color: #f1f5f9;">
            <h1>❌ Error: client.html not found</h1>
            <p>Please ensure client.html is in the same directory as server.py</p>
        </body>
        </html>
        ''', 404, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f'''
        <html>
        <body style="font-family: Arial; padding: 50px; background: #0f172a; color: #f1f5f9;">
            <h1>❌ Error loading client</h1>
            <p>Error: {str(e)}</p>
        </body>
        </html>
        ''', 500, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/status')
def status():
    """Server status page"""
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
            <li><strong>Web Interface:</strong> <code>http://localhost:4564</code></li>
            <li><strong>WebSocket:</strong> <code>ws://localhost:4564</code></li>
            <li><strong>Status:</strong> <code>http://localhost:4564/status</code> (this page)</li>
            <li><strong>Health:</strong> <code>http://localhost:4564/health</code></li>
        </ul>
        <h3>Next Steps:</h3>
        <ol>
            <li>Visit <code>http://localhost:4564</code> in your browser</li>
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
    print("🌐 Web Interface: http://localhost:4564")
    print("📡 WebSocket: ws://localhost:4564")
    print("🤖 Models: MediaPipe Hands + Face Mesh")
    print("=" * 60)
    print("\n✨ Open http://localhost:4564 in your browser to start!\n")
    print("Press Ctrl+C to stop the server\n")

    socketio.run(app, host='0.0.0.0', port=4564, debug=True, allow_unsafe_werkzeug=True)
