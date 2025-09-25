from flask import Flask, Response, request, jsonify
from flask_cors import CORS
import cv2
import numpy as np
import faiss
from insightface.app import FaceAnalysis
import os 
from dotenv import load_dotenv
from sklearn.metrics.pairwise import cosine_similarity
import time
import psycopg2
import threading
import queue
import traceback
import logging
from datetime import datetime

load_dotenv()

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FaceTrackingProcessor:
    def __init__(self):
        self.frame_queue = queue.Queue(maxsize=5)
        self.output_queue = queue.Queue(maxsize=2)
        self.stop_flag = threading.Event()
        self.processing_error = threading.Event()
        
        self.cap = None
        self.processing_thread = None
        self.capture_thread = None
        
        # Initialize face detection
        self.face_app = FaceAnalysis(name="buffalo_l")
        self.face_app.prepare(ctx_id=0, det_size=(640, 640))
        
        # Initialize FAISS index
        dim = 512
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
        
        # Tracking variables
        self.next_id = 0
        self.id2emb = {}
        self.id_checked_in_db = {}
        self.id_suspicious_status = {}
        
        # Configuration
        self.top_k = 1
        self.threshold = 0.45
        self.tracking_threshold = 0.4
        
        # Database connection
        self.conn = None
        self.stored_embeddings = []
        self.stored_labels = []
        
        self.is_streaming = False
        self.stream_url = None
        
        self.initialize_database()
        self.load_embeddings_from_db()
    
    def initialize_database(self):
        try:
            db = os.getenv("PG_DB")
            user = os.getenv("PG_USERNAME")
            password = os.getenv("PG_PASSWORD")
            host = os.getenv("PG_HOST")
            port = os.getenv("PG_PORT")
            
            self.conn = psycopg2.connect(
                dbname=db, user=user, password=password,
                host=host, port=port, sslmode="require"
            )
            logger.info("Postgres connected successfully")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
    
    def load_embeddings_from_db(self):
        if not self.conn:
            logger.error("No database connection")
            return
            
        try:
            cur = self.conn.cursor()
            cur.execute("""
                SELECT id, name, nickname, age, police_station, crime_and_section, 
                    head_of_crime, arrested_date, img_url, embedding
                FROM criminal_records;
            """)
            rows = cur.fetchall()
            
            self.stored_embeddings = []
            self.stored_labels = []
            
            for row in rows:
                embedding = np.array(row[9])
                self.stored_embeddings.append(embedding)
                
                info = {
                    "id": row[0], "name": row[1], "nickname": row[2],
                    "age": row[3], "police_station": row[4],
                    "crime_and_section": row[5], "head_of_crime": row[6],
                    "arrested_date": row[7], "img_url": row[8]
                }
                self.stored_labels.append(info)
            
            if self.stored_embeddings:
                self.stored_embeddings = np.stack(self.stored_embeddings)
            else:
                self.stored_embeddings = np.zeros((0, 512))
            
            logger.info(f"Loaded {len(self.stored_labels)} embeddings from database")
        except Exception as e:
            logger.error(f"Error loading embeddings: {e}")
    
    def start_stream(self, stream_url):
        if self.is_streaming:
            self.stop_stream()
        
        self.stream_url = stream_url
        self.stop_flag.clear()
        self.processing_error.clear()
        
        # Handle different input types
        if stream_url.isdigit():
            stream_url = int(stream_url)
        
        self.cap = cv2.VideoCapture(stream_url)
        if not self.cap.isOpened():
            logger.error(f"Cannot open stream: {stream_url}")
            return False
        
        self.capture_thread = threading.Thread(target=self._capture_frames, daemon=True)
        self.processing_thread = threading.Thread(target=self._process_frames, daemon=True)
        
        self.capture_thread.start()
        self.processing_thread.start()
        
        self.is_streaming = True
        logger.info(f"Started streaming from: {stream_url}")
        return True
    
    def stop_stream(self):
        logger.info("Stopping stream...")
        self.stop_flag.set()
        self.is_streaming = False
        
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2)
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=2)
        
        if self.cap:
            self.cap.release()
            self.cap = None
        
        self._clear_queues()
        logger.info("Stream stopped")
    
    def _clear_queues(self):
        try:
            while not self.frame_queue.empty():
                self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            while not self.output_queue.empty():
                self.output_queue.get_nowait()
        except queue.Empty:
            pass
    
    def _capture_frames(self):
        target_fps = 2
        frame_time = 1 / target_fps
        last_time = 0
        
        try:
            while not self.stop_flag.is_set() and self.cap and self.cap.isOpened():
                ret = self.cap.grab()
                if not ret:
                    logger.warning("Failed to grab frame")
                    break
                
                now = time.time()
                if (now - last_time) >= frame_time:
                    last_time = now
                    ret, frame = self.cap.retrieve()
                    if ret:
                        try:
                            self.frame_queue.put_nowait((frame, now))
                        except queue.Full:
                            pass
        except Exception as e:
            logger.error(f"Error in capture thread: {e}")
            traceback.print_exc()
        finally:
            logger.info("Capture thread exiting...")
    
    def _process_frames(self):
        try:
            while not self.stop_flag.is_set():
                try:
                    item = self.frame_queue.get(timeout=0.1)
                    if item is None:
                        break
                except queue.Empty:
                    continue
                
                try:
                    frame, timestamp = item
                    resized_frame = cv2.resize(frame, (1024, 640), interpolation=cv2.INTER_AREA)
                    faces = self.face_app.get(resized_frame)
                    
                    for face in faces:
                        if self.stop_flag.is_set():
                            break
                        
                        if face.det_score < 0.7:
                            continue
                        
                        x1, y1, x2, y2 = map(int, face.bbox)
                        query_emb = face.embedding.astype("float32")
                        query_emb /= np.linalg.norm(query_emb)
                        query_emb = query_emb.reshape(1, -1)

                        assigned_id = None
                        if self.index.ntotal > 0:
                            sims, ids = self.index.search(query_emb, 1)
                            best_sim, best_id = sims[0][0], ids[0][0]
                            if best_sim > self.tracking_threshold:
                                assigned_id = best_id

                        if assigned_id is None:
                            assigned_id = self.next_id
                            self.index.add_with_ids(query_emb, np.array([assigned_id], dtype=np.int64))
                            self.id2emb[assigned_id] = query_emb.flatten()
                            self.id_checked_in_db[assigned_id] = False
                            self.next_id += 1
                            logger.info(f"New face detected with ID: {assigned_id}")
                        else:
                            old_emb = self.id2emb[assigned_id]
                            new_emb = 0.7 * old_emb + 0.3 * query_emb.flatten()
                            new_emb /= np.linalg.norm(new_emb)
                            self.id2emb[assigned_id] = new_emb
                            self.index.remove_ids(np.array([assigned_id], dtype=np.int64))
                            self.index.add_with_ids(new_emb.reshape(1, -1), np.array([assigned_id], dtype=np.int64))

                        if not self.id_checked_in_db.get(assigned_id, False):
                            self.id_checked_in_db[assigned_id] = True
                            if len(self.stored_embeddings) > 0:
                                current_emb = self.id2emb[assigned_id].reshape(1, -1)
                                sims_db = cosine_similarity(current_emb, self.stored_embeddings)[0]
                                top_indices = np.argsort(sims_db)[::-1][:self.top_k]
                                results = []
                                for idx in top_indices:
                                    score = sims_db[idx]
                                    if score > self.threshold:
                                        results.append({**self.stored_labels[idx], "score": float(score)})
                                if len(results) > 0:
                                    self.id_suspicious_status[assigned_id] = True
                                    logger.info(f"SUSPICIOUS ID {assigned_id}: {results[0]}")
                                else:
                                    self.id_suspicious_status[assigned_id] = False
                                    logger.info(f"Clean ID {assigned_id}")
                            else:
                                self.id_suspicious_status[assigned_id] = False

                        color = (0, 0, 255) if self.id_suspicious_status.get(assigned_id, False) else (0, 255, 0)
                        status_text = "SUSPICIOUS" if self.id_suspicious_status.get(assigned_id, False) else "CLEAN"
                        
                        cv2.rectangle(resized_frame, (x1, y1), (x2, y2), color, 2)
                        cv2.putText(resized_frame, f"ID: {assigned_id} ({status_text})", 
                                   (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
                    cv2.putText(resized_frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    
                    if not self.stop_flag.is_set():
                        try:
                            self.output_queue.put_nowait(resized_frame)
                        except queue.Full:
                            pass
                            
                except Exception as e:
                    logger.error(f"Error processing frame: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Fatal error in processing thread: {e}")
            self.processing_error.set()
            self.stop_flag.set()
        finally:
            logger.info("Processing thread exiting...")
    
    def get_frame(self):
        try:
            frame = self.output_queue.get(timeout=0.1)
            return frame
        except queue.Empty:
            return None
    
    def get_stats(self):
        return {
            'total_faces': len(self.id2emb),
            'suspicious_faces': sum(self.id_suspicious_status.values()),
            'clean_faces': len(self.id2emb) - sum(self.id_suspicious_status.values()),
            'database_entries': len(self.stored_labels)
        }

processor = FaceTrackingProcessor()

def generate_frames():
    while processor.is_streaming:
        frame = processor.get_frame()
        if frame is not None:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                frame_bytes = buffer.tobytes()
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        else:
            time.sleep(0.01)

@app.route('/')
def index():
    return jsonify({
        'status': 'running',
        'streaming': processor.is_streaming,
        'stream_url': processor.stream_url,
        'stats': processor.get_stats()
    })

@app.route('/api/start_stream', methods=['POST'])
def start_stream():
    try:
        data = request.json
        stream_url = data.get('url')
        
        if not stream_url:
            return jsonify({'error': 'URL is required'}), 400
        
        success = processor.start_stream(stream_url)
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Stream started from {stream_url}',
                'stream_url': stream_url
            })
        else:
            return jsonify({'error': 'Failed to start stream'}), 500
            
    except Exception as e:
        logger.error(f"Error starting stream: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stop_stream', methods=['POST'])
def stop_stream():
    try:
        processor.stop_stream()
        return jsonify({
            'status': 'success',
            'message': 'Stream stopped'
        })
    except Exception as e:
        logger.error(f"Error stopping stream: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stream_status')
def stream_status():
    return jsonify({
        'streaming': processor.is_streaming,
        'stream_url': processor.stream_url,
        'has_error': processor.processing_error.is_set(),
        'stats': processor.get_stats()
    })

@app.route('/video_feed')
def video_feed():
    if not processor.is_streaming:
        return jsonify({'error': 'No active stream'}), 404
    
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/reload_db', methods=['POST'])
def reload_database():
    try:
        processor.load_embeddings_from_db()
        return jsonify({
            'status': 'success',
            'message': 'Database reloaded',
            'entries_loaded': len(processor.stored_labels)
        })
    except Exception as e:
        logger.error(f"Error reloading database: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def get_stats():
    return jsonify(processor.get_stats())

if __name__ == '__main__':
    try:
        logger.info("Starting Flask Face Tracking Backend...")
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        processor.stop_stream()
    except Exception as e:
        logger.error(f"Error starting server: {e}")
        processor.stop_stream()