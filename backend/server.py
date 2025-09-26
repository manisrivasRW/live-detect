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
import logging
from datetime import datetime
import uuid

load_dotenv()

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- Shared Face Tracker ----------------- #
class SharedFaceTracker:
    def __init__(self):
        self.lock = threading.RLock()
        
        dim = 512
        self.index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))
        self.next_id = 0
        self.id2emb = {}
        self.id_checked_in_db = {}
        self.id_suspicious_status = {}
        self.suspicious_map = {}
        self.id2last_bbox = {}  # last bbox per ID
        self.id2last_seen = {}  # timestamp when ID was last seen
        self.id_similarity_matrix = {}  # track similarities between IDs
        self.id2stream = {}  # stream ownership per ID
        self.pending_tracks = {}  # temporary tracks before assigning persistent IDs
        self.lifetime_ids = set()  # stable count: all IDs ever assigned this run
        self.lifetime_suspicious_ids = set()  # stable suspicious IDs seen this run
        self.relink_tracks = {}  # probationary re-linking of old global IDs after gaps
        
        # Config
        self.top_k = 1
        self.threshold = 0.45
        self.tracking_threshold = 0.50  # slightly lower to allow reuse under occlusion
        self.faces_since_rebuild = 0
        self.rebuild_interval = 100  # Increased rebuild interval
        self.min_face_size = 24  # Minimum face size in pixels (improves small-face detection)
        self.max_faces_per_frame = 30  # Limit faces per frame
        self.face_timeout = 30  # Remove faces not seen for 30 seconds
        self.consolidation_threshold = 0.65  # Threshold for ID consolidation (merge near-duplicates)
        self.consolidation_check_interval = 20  # Check for consolidation every 20 faces
        self.reuse_distance_px = 120  # Spatial distance threshold for reuse
        self.reuse_time_window_s = 3.0  # Time window for spatial-temporal reuse
        self.min_appearances_for_id = 3  # require N appearances before creating ID
        self.pending_timeout_s = 3.0  # pending track expiry
        self.similarity_reuse_threshold = 0.65  # reuse existing ID if cosine >= this
        self.relink_duration_s = 3.0  # require continuous presence before re-link
        self.relink_min_confidence = 0.35  # minimum cosine similarity to re-link
        # Immediate duplicate resolution
        self.immediate_merge_threshold = 0.8  # if two active IDs exceed this cosine, merge now
        self.immediate_merge_iou = 0.45  # or if strong spatial overlap
        self.immediate_merge_time_window = 2.0  # seen within this many seconds

        # DB connection
        self.conn = None
        self.stored_embeddings = []
        self.stored_labels = []
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
            logger.info("Shared Postgres connection established")
        except Exception as e:
            logger.error(f"Shared database connection failed: {e}")
    
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
                embedding = np.array(row[9], dtype=np.float32)
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

    # ----------------- Per-frame suppression ----------------- #
    @staticmethod
    def iou(boxA, boxB):
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
        boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
        boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
        return interArea / float(boxAArea + boxBArea - interArea)

    def process_face(self, face_embedding, bbox, stream_id):
        with self.lock:
            # Normalize embedding
            query_emb = face_embedding.astype("float32")
            query_emb /= np.linalg.norm(query_emb)
            query_emb = query_emb.reshape(1, -1)

            # Check face size
            face_width = bbox[2] - bbox[0]
            face_height = bbox[3] - bbox[1]
            if face_width < self.min_face_size or face_height < self.min_face_size:
                return None, False, bbox

            assigned_id = None
            best_sim = 0
            
            # ----------------- Fast spatial-temporal reuse (same stream) ----------------- #
            # If a recent ID was seen very close in space in the same stream, reuse it
            center_x = (bbox[0] + bbox[2]) // 2
            center_y = (bbox[1] + bbox[3]) // 2
            now_ts = time.time()
            for existing_id, last_bbox in list(self.id2last_bbox.items()):
                if self.id2stream.get(existing_id) != stream_id:
                    continue
                last_seen = self.id2last_seen.get(existing_id, 0)
                if now_ts - last_seen > self.reuse_time_window_s:
                    continue
                # Compute center distance
                lx = (last_bbox[0] + last_bbox[2]) // 2
                ly = (last_bbox[1] + last_bbox[3]) // 2
                dist = ((center_x - lx) ** 2 + (center_y - ly) ** 2) ** 0.5
                if dist <= self.reuse_distance_px:
                    assigned_id = existing_id
                    best_sim = 1.0
                    break

            # Search for existing face with higher threshold and multiple candidates
            if self.index.ntotal > 0:
                sims, ids = self.index.search(query_emb, min(10, self.index.ntotal))
                for i in range(len(sims[0])):
                    sim = sims[0][i]
                    face_id = ids[0][i]
                    
                    # Check IoU with last known bbox for this ID
                    last_bbox = self.id2last_bbox.get(face_id)
                    if last_bbox and self.iou(last_bbox, bbox) > 0.3:  # Spatial consistency
                        if sim > self.tracking_threshold and sim > best_sim:
                            # Temporal re-link probation: don't immediately re-activate old global ID
                            candidate_id = face_id
                            state = self.relink_tracks.get(candidate_id)
                            now_ts = time.time()
                            if state is None:
                                self.relink_tracks[candidate_id] = {
                                    'start_ts': now_ts,
                                    'last_ts': now_ts,
                                    'best_sim': float(sim),
                                }
                                # Do not assign yet; wait for probation window
                                continue
                            else:
                                # update state
                                state['last_ts'] = now_ts
                                state['best_sim'] = max(state['best_sim'], float(sim))
                                if (now_ts - state['start_ts'] >= self.relink_duration_s 
                                    and state['best_sim'] >= self.relink_min_confidence):
                                    assigned_id = candidate_id
                                    best_sim = sim
                                else:
                                    continue
                    elif sim > self.tracking_threshold + 0.15:  # Even higher threshold without spatial info
                        if sim > best_sim:
                            # same probation rule even without spatial info when similarity is high
                            candidate_id = face_id
                            state = self.relink_tracks.get(candidate_id)
                            now_ts = time.time()
                            if state is None:
                                self.relink_tracks[candidate_id] = {
                                    'start_ts': now_ts,
                                    'last_ts': now_ts,
                                    'best_sim': float(sim),
                                }
                                continue
                            else:
                                state['last_ts'] = now_ts
                                state['best_sim'] = max(state['best_sim'], float(sim))
                                if (now_ts - state['start_ts'] >= self.relink_duration_s 
                                    and state['best_sim'] >= self.relink_min_confidence):
                                    assigned_id = candidate_id
                                    best_sim = sim
                                else:
                                    continue
                    
                    # Additional check: if similarity is very high, use it regardless of spatial info
                    if sim > 0.8 and sim > best_sim:
                        candidate_id = face_id
                        state = self.relink_tracks.get(candidate_id)
                        now_ts = time.time()
                        if state is None:
                            self.relink_tracks[candidate_id] = {
                                'start_ts': now_ts,
                                'last_ts': now_ts,
                                'best_sim': float(sim),
                            }
                            continue
                        else:
                            state['last_ts'] = now_ts
                            state['best_sim'] = max(state['best_sim'], float(sim))
                            if (now_ts - state['start_ts'] >= self.relink_duration_s 
                                and state['best_sim'] >= self.relink_min_confidence):
                                assigned_id = candidate_id
                                best_sim = sim
                            else:
                                continue

            # ----------------- Assign new ID or update existing ----------------- #
            occluded_id = None
            recent_nearby_exists = False
            if assigned_id is None:
                # Stage into pending tracks until stable across frames
                # Key by stream and spatial cell to avoid fragmentation
                cell_size = 64
                cell_key = (stream_id, (center_x // cell_size, center_y // cell_size))
                now_ts = time.time()
                pending = self.pending_tracks.get(cell_key)
                if pending is None:
                    self.pending_tracks[cell_key] = {
                        'count': 1,
                        'first_ts': now_ts,
                        'last_ts': now_ts,
                        'emb': query_emb.flatten(),
                        'bbox': bbox
                    }
                else:
                    # update running average embedding and bbox
                    pending['count'] += 1
                    pending['last_ts'] = now_ts
                    emb = pending['emb']
                    emb = 0.7 * emb + 0.3 * query_emb.flatten()
                    emb /= np.linalg.norm(emb)
                    pending['emb'] = emb
                    pending['bbox'] = bbox

                # Promote to persistent ID once stable enough
                promote = False
                if self.pending_tracks[cell_key]['count'] >= self.min_appearances_for_id:
                    promote = True
                # Also auto-promote if a very similar existing ID found
                matched_existing = None
                if not promote and len(self.id2emb) > 0:
                    all_embs = np.stack(list(self.id2emb.values()))
                    all_ids = list(self.id2emb.keys())
                    similarities = cosine_similarity(query_emb, all_embs)[0]
                    max_idx = int(np.argmax(similarities))
                    if similarities[max_idx] > 0.8:
                        matched_existing = all_ids[max_idx]

                if matched_existing is not None:
                    assigned_id = matched_existing
                    best_sim = max(best_sim, 0.8)
                elif promote:
                    # Clean stale pending tracks
                    self._cleanup_pending(now_ts)
                    
                    # Create new permanent ID
                    if len(self.id2emb) >= 1000:
                        logger.warning("Maximum face capacity reached, skipping new face")
                        return None, False, bbox
                    assigned_id = self.next_id
                    emb = self.pending_tracks[cell_key]['emb']
                    self.index.add_with_ids(emb.reshape(1, -1), np.array([assigned_id], dtype=np.int64))
                    self.id2emb[assigned_id] = emb
                    self.id_checked_in_db[assigned_id] = False
                    self.id_suspicious_status[assigned_id] = False
                    self.next_id += 1
                    self.faces_since_rebuild += 1
                    # lifetime accounting
                    self.lifetime_ids.add(assigned_id)
                    logger.info(f"Promoted new face to ID: {assigned_id} from stream: {stream_id}")
                    # Remove pending entry
                    self.pending_tracks.pop(cell_key, None)
                else:
                    # Occlusion fallback: if a very recent bbox exists nearby in this stream, reuse its ID
                    for existing_id, last_bbox in list(self.id2last_bbox.items()):
                        if self.id2stream.get(existing_id) != stream_id:
                            continue
                        last_seen = self.id2last_seen.get(existing_id, 0)
                        if now_ts - last_seen > self.reuse_time_window_s:
                            continue
                        # Check IoU or center distance
                        iou_val = self.iou(bbox, last_bbox)
                        lx = (last_bbox[0] + last_bbox[2]) // 2
                        ly = (last_bbox[1] + last_bbox[3]) // 2
                        dist = ((center_x - lx) ** 2 + (center_y - ly) ** 2) ** 0.5
                        if iou_val > 0.2 or dist <= self.reuse_distance_px:
                            recent_nearby_exists = True
                            occluded_id = existing_id
                            break

                if occluded_id is not None:
                    assigned_id = occluded_id
                    best_sim = max(best_sim, 0.6)  # moderate confidence
                else:
                    # Double-check: look for any very similar existing faces before creating new ID
                    if len(self.id2emb) > 0:
                        all_embs = np.stack(list(self.id2emb.values()))
                        all_ids = list(self.id2emb.keys())
                        similarities = cosine_similarity(query_emb, all_embs)[0]
                        max_sim_idx = np.argmax(similarities)
                        max_similarity = similarities[max_sim_idx]
                        
                        if max_similarity > self.similarity_reuse_threshold:  # Reuse even with moderate similarity
                            candidate_id = all_ids[max_sim_idx]
                            now_ts = time.time()
                            state = self.relink_tracks.get(candidate_id)
                            if state is None:
                                self.relink_tracks[candidate_id] = {
                                    'start_ts': now_ts,
                                    'last_ts': now_ts,
                                    'best_sim': float(max_similarity),
                                }
                            else:
                                state['last_ts'] = now_ts
                                state['best_sim'] = max(state['best_sim'], float(max_similarity))
                                if (now_ts - state['start_ts'] >= self.relink_duration_s 
                                    and state['best_sim'] >= self.relink_min_confidence):
                                    assigned_id = candidate_id
                                    logger.info(f"Re-linked existing ID after probation: {assigned_id}")
                        else:
                            # Strict anti-duplication: if there is a recent nearby ID, do not create a new one yet
                            if recent_nearby_exists:
                                return None, False, bbox
                            # Check if we're at max capacity
                            if len(self.id2emb) >= 1000:  # Reasonable limit
                                logger.warning("Maximum face capacity reached, skipping new face")
                                return None, False, bbox
                            
                            assigned_id = self.next_id
                            self.index.add_with_ids(query_emb, np.array([assigned_id], dtype=np.int64))
                            self.id2emb[assigned_id] = query_emb.flatten()
                            self.id_checked_in_db[assigned_id] = False
                            self.id_suspicious_status[assigned_id] = False
                            self.next_id += 1
                            self.faces_since_rebuild += 1
                            # lifetime accounting
                            self.lifetime_ids.add(assigned_id)
                            logger.info(f"New face detected with ID: {assigned_id} from stream: {stream_id}")
                    else:
                        # First face ever
                        assigned_id = self.next_id
                        self.index.add_with_ids(query_emb, np.array([assigned_id], dtype=np.int64))
                        self.id2emb[assigned_id] = query_emb.flatten()
                        self.id_checked_in_db[assigned_id] = False
                        self.id_suspicious_status[assigned_id] = False
                        self.next_id += 1
                        self.faces_since_rebuild += 1
                        # lifetime accounting
                        self.lifetime_ids.add(assigned_id)
                        logger.info(f"New face detected with ID: {assigned_id} from stream: {stream_id}")
            else:
                # Update existing face embedding with better weighting
                old_emb = self.id2emb[assigned_id]
                # Use adaptive weighting based on similarity
                weight = min(0.5, best_sim * 0.3)  # Higher similarity = more weight to new embedding
                new_emb = (1 - weight) * old_emb + weight * query_emb.flatten()
                new_emb /= np.linalg.norm(new_emb)
                self.id2emb[assigned_id] = new_emb
                
                # Update FAISS index
                self.index.remove_ids(np.array([assigned_id], dtype=np.int64))
                self.index.add_with_ids(new_emb.reshape(1, -1), np.array([assigned_id], dtype=np.int64))

            # ----------------- Update bbox with smoothing ----------------- #
            last_bbox = self.id2last_bbox.get(assigned_id)
            if last_bbox:
                # Smooth bbox changes to reduce jitter
                alpha = 0.3
                smoothed_bbox = (
                    int(alpha * bbox[0] + (1 - alpha) * last_bbox[0]),
                    int(alpha * bbox[1] + (1 - alpha) * last_bbox[1]),
                    int(alpha * bbox[2] + (1 - alpha) * last_bbox[2]),
                    int(alpha * bbox[3] + (1 - alpha) * last_bbox[3])
                )
                bbox = smoothed_bbox
            self.id2last_bbox[assigned_id] = bbox
            self.id2last_seen[assigned_id] = time.time()
            self.id2stream[assigned_id] = stream_id
            # Clear probation state once ID is officially active again
            self.relink_tracks.pop(assigned_id, None)

            # ----------------- Check against database embeddings ----------------- #
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
                        self.suspicious_map[assigned_id] = results[0]
                        self.lifetime_suspicious_ids.add(assigned_id)
                        logger.info(f"SUSPICIOUS ID {assigned_id} from stream {stream_id}: {results[0]}")
                    else:
                        self.id_suspicious_status[assigned_id] = False
                        logger.info(f"Clean ID {assigned_id} from stream {stream_id}")
                else:
                    self.id_suspicious_status[assigned_id] = False

            # ----------------- Periodic consolidation check ----------------- #
            if self.faces_since_rebuild % self.consolidation_check_interval == 0:
                self.consolidate_duplicate_ids()
            
            # ----------------- Periodic cleanup and rebuild ----------------- #
            if self.faces_since_rebuild >= self.rebuild_interval:
                # Clean up old faces first
                self.cleanup_old_faces()
                
                # Consolidate duplicates before rebuild
                self.consolidate_duplicate_ids()
                
                # Rebuild FAISS index
                all_ids = np.array(list(self.id2emb.keys()), dtype=np.int64)
                if len(all_ids) > 0:
                    all_embs = np.stack(list(self.id2emb.values()))
                    self.index = faiss.IndexIDMap(faiss.IndexFlatIP(all_embs.shape[1]))
                    self.index.add_with_ids(all_embs, all_ids)
                    logger.info(f"FAISS index rebuilt with {len(all_ids)} faces")
                else:
                    self.index = faiss.IndexIDMap(faiss.IndexFlatIP(512))
                    logger.info("FAISS index rebuilt (empty)")
                self.faces_since_rebuild = 0

            return assigned_id, self.id_suspicious_status.get(assigned_id, False), bbox

    def consolidate_duplicate_ids(self):
        """Consolidate IDs that belong to the same person"""
        if len(self.id2emb) < 2:
            return
        
        ids = list(self.id2emb.keys())
        consolidated = set()
        
        for i, id1 in enumerate(ids):
            if id1 in consolidated:
                continue
                
            emb1 = self.id2emb[id1].reshape(1, -1)
            similar_ids = [id1]  # Start with current ID
            
            for j, id2 in enumerate(ids[i+1:], i+1):
                if id2 in consolidated:
                    continue
                    
                emb2 = self.id2emb[id2].reshape(1, -1)
                similarity = cosine_similarity(emb1, emb2)[0][0]
                
                # Prefer immediate merge if very similar or overlapping recently
                iou_recent = 0
                last1 = self.id2last_bbox.get(id1)
                last2 = self.id2last_bbox.get(id2)
                if last1 is not None and last2 is not None:
                    iou_recent = self.iou(last1, last2)
                seen_close = False
                t1 = self.id2last_seen.get(id1, 0)
                t2 = self.id2last_seen.get(id2, 0)
                if abs(t1 - t2) <= self.immediate_merge_time_window:
                    seen_close = True

                if (similarity >= self.immediate_merge_threshold and seen_close) or iou_recent >= self.immediate_merge_iou:
                    similar_ids.append(id2)
                    consolidated.add(id2)
                elif similarity > self.consolidation_threshold:
                    similar_ids.append(id2)
                    consolidated.add(id2)
            
            # If we found similar IDs, consolidate them
            if len(similar_ids) > 1:
                logger.info(f"Consolidating IDs {similar_ids} (similarity > {self.consolidation_threshold})")
                
                # Keep the oldest ID (lowest number)
                primary_id = min(similar_ids)
                other_ids = [id for id in similar_ids if id != primary_id]
                
                # Merge embeddings (weighted average)
                primary_emb = self.id2emb[primary_id]
                for other_id in other_ids:
                    other_emb = self.id2emb[other_id]
                    # Weight by recency (more recent = higher weight)
                    weight = 0.3
                    primary_emb = (1 - weight) * primary_emb + weight * other_emb
                    primary_emb /= np.linalg.norm(primary_emb)
                
                # Update primary ID with merged embedding
                self.id2emb[primary_id] = primary_emb
                
                # Transfer suspicious status if any of the other IDs were suspicious
                for other_id in other_ids:
                    if self.id_suspicious_status.get(other_id, False):
                        self.id_suspicious_status[primary_id] = True
                        if other_id in self.suspicious_map:
                            self.suspicious_map[primary_id] = self.suspicious_map[other_id]
                
                # Remove other IDs from all tracking structures
                for other_id in other_ids:
                    self.index.remove_ids(np.array([other_id], dtype=np.int64))
                    self.id2emb.pop(other_id, None)
                    self.id_checked_in_db.pop(other_id, None)
                    self.id_suspicious_status.pop(other_id, None)
                    self.suspicious_map.pop(other_id, None)
                    self.id2last_bbox.pop(other_id, None)
                    self.id2last_seen.pop(other_id, None)
                    self.id2stream.pop(other_id, None)
                
                # Update FAISS index with consolidated embedding
                self.index.remove_ids(np.array([primary_id], dtype=np.int64))
                self.index.add_with_ids(primary_emb.reshape(1, -1), np.array([primary_id], dtype=np.int64))

    def cleanup_old_faces(self):
        """Remove faces that haven't been seen for a while"""
        current_time = time.time()
        to_remove = []
        
        for face_id, last_seen in self.id2last_seen.items():
            if current_time - last_seen > self.face_timeout:
                to_remove.append(face_id)
        
        if to_remove:
            logger.info(f"Cleaning up {len(to_remove)} old faces")
            for face_id in to_remove:
                # Remove from FAISS index
                self.index.remove_ids(np.array([face_id], dtype=np.int64))
                
                # Remove from all tracking dictionaries
                self.id2emb.pop(face_id, None)
                self.id_checked_in_db.pop(face_id, None)
                self.id_suspicious_status.pop(face_id, None)
                self.suspicious_map.pop(face_id, None)
                self.id2last_bbox.pop(face_id, None)
                self.id2last_seen.pop(face_id, None)
                self.id2stream.pop(face_id, None)

        # Cleanup stale pending tracks
        self._cleanup_pending(current_time)

    def _cleanup_pending(self, now_ts=None):
        if now_ts is None:
            now_ts = time.time()
        stale_keys = []
        for key, entry in self.pending_tracks.items():
            if now_ts - entry.get('last_ts', entry.get('first_ts', now_ts)) > self.pending_timeout_s:
                stale_keys.append(key)
        for key in stale_keys:
            self.pending_tracks.pop(key, None)
        # Cleanup relink probation entries that have not updated for a while
        stale_ids = []
        for rid, state in self.relink_tracks.items():
            if now_ts - state.get('last_ts', state.get('start_ts', now_ts)) > self.pending_timeout_s:
                stale_ids.append(rid)
        for rid in stale_ids:
            self.relink_tracks.pop(rid, None)

    def get_stats(self):
        with self.lock:
            current_time = time.time()
            active_faces = sum(1 for last_seen in self.id2last_seen.values() 
                             if current_time - last_seen < self.face_timeout)
            
            suspicious_now = sum(self.id_suspicious_status.values())
            total_now = len(self.id2emb)
            clean_now = total_now - suspicious_now
            return {
                'total_faces': total_now,
                'lifetime_faces': len(self.lifetime_ids),
                'active_faces': active_faces,
                'suspicious_faces': suspicious_now,
                'clean_faces': clean_now,
                'database_entries': len(self.stored_labels),
                'suspicious_ids': [id for id, status in self.id_suspicious_status.items() if status],
                'tracking_threshold': self.tracking_threshold,
                'consolidation_threshold': self.consolidation_threshold,
                'face_timeout': self.face_timeout,
                'next_id': self.next_id,
                'consolidation_check_interval': self.consolidation_check_interval
            }
    
    def get_suspicious_data(self):
        with self.lock:
            return list(self.suspicious_map.values())

# Global shared tracker
shared_tracker = SharedFaceTracker()

class StreamProcessor:
    def __init__(self, stream_id):
        self.stream_id = stream_id
        self.frame_queue = queue.Queue(maxsize=5)
        self.output_queue = queue.Queue(maxsize=2)
        self.stop_flag = threading.Event()
        self.processing_error = threading.Event()
        
        self.cap = None
        self.processing_thread = None
        self.capture_thread = None
        
        # Initialize face detection (use larger det_size for small faces)
        self.face_app = FaceAnalysis(name="buffalo_l")
        self.face_app.prepare(ctx_id=0, det_size=(1280, 1280))
        
        self.is_streaming = False
        self.stream_url = None
    
    def start_stream(self, stream_url):
        if self.is_streaming:
            self.stop_stream()
        
        self.stream_url = stream_url
        self.stop_flag.clear()
        self.processing_error.clear()
        
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
        logger.info(f"Started stream {self.stream_id} from: {stream_url}")
        return True
    
    def stop_stream(self):
        logger.info(f"Stopping stream {self.stream_id}...")
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
        logger.info(f"Stream {self.stream_id} stopped")
    
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
        next_frame_ts = time.time()
        
        try:
            while not self.stop_flag.is_set() and self.cap and self.cap.isOpened():
                now = time.time()
                remaining = next_frame_ts - now
                if remaining > 0:
                    # Sleep a bit until the next frame deadline
                    time.sleep(min(remaining, 0.005))
                    continue

                # Time to capture a frame
                grabbed = self.cap.grab()
                if not grabbed:
                    logger.warning(f"Failed to grab frame for stream {self.stream_id}")
                    # Skip ahead to the next scheduled timestamp
                    next_frame_ts = now + frame_time
                    continue

                ret, frame = self.cap.retrieve()
                if ret:
                    capture_ts = time.time()
                    try:
                        self.frame_queue.put_nowait((frame, capture_ts))
                    except queue.Full:
                        # Drop if processing is behind
                        pass

                # Schedule next frame; if we're behind, catch up without piling up
                next_frame_ts += frame_time
                if next_frame_ts < time.time() - frame_time:
                    next_frame_ts = time.time()
        except Exception as e:
            logger.error(f"Error in capture thread for stream {self.stream_id}: {e}")
        finally:
            logger.info(f"Capture thread exiting for stream {self.stream_id}...")
    
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
                    resized_frame = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
                    faces = self.face_app.get(resized_frame)
                    
                    # Fallback: if few or no faces found, try multi-scale upsample
                    if len(faces) <= 1:
                        for scale in [1.25, 1.5, 1.75]:
                            upsampled = cv2.resize(resized_frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
                            faces = self.face_app.get(upsampled)
                            if len(faces) > 0:
                                scale_x = upsampled.shape[1] / resized_frame.shape[1]
                                scale_y = upsampled.shape[0] / resized_frame.shape[0]
                                # Scale bboxes back to resized_frame coordinates
                                for f in faces:
                                    x1, y1, x2, y2 = f.bbox
                                    f.bbox = np.array([x1/scale_x, y1/scale_y, x2/scale_x, y2/scale_y])
                                break
                    
                    # Sort faces by detection score and limit number
                    faces = sorted(faces, key=lambda x: x.det_score, reverse=True)
                    faces = faces[:shared_tracker.max_faces_per_frame]
                    
                    processed_faces = []
                    for face in faces:
                        if self.stop_flag.is_set():
                            break
                        
                        # Lower gate so more clear faces pass detection
                        if face.det_score < 0.5:
                            continue
                        
                        x1, y1, x2, y2 = map(int, face.bbox)
                        bbox = (x1, y1, x2, y2)
                        
                        # Check for overlap with already processed faces
                        overlap = False
                        for prev_bbox in processed_faces:
                            if shared_tracker.iou(bbox, prev_bbox) > 0.3:
                                overlap = True
                                break
                        
                        if overlap:
                            continue
                        
                        assigned_id, is_suspicious, draw_bbox = shared_tracker.process_face(
                            face.embedding, bbox, self.stream_id
                        )
                        
                        if assigned_id is not None:  # Only draw if face was processed
                            processed_faces.append(draw_bbox)
                            color = (0, 0, 255) if is_suspicious else (0, 255, 0)
                            status_text = "SUSPICIOUS" if is_suspicious else "CLEAN"
                            cv2.rectangle(resized_frame, (draw_bbox[0], draw_bbox[1]), 
                                        (draw_bbox[2], draw_bbox[3]), color, 2)
                            cv2.putText(resized_frame, f"ID: {assigned_id} ({status_text})", 
                                       (draw_bbox[0], draw_bbox[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    
                    cv2.putText(resized_frame, f"Stream: {self.stream_id} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                               (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    
                    if not self.stop_flag.is_set():
                        try:
                            self.output_queue.put_nowait(resized_frame)
                        except queue.Full:
                            pass
                            
                except Exception as e:
                    logger.error(f"Error processing frame for stream {self.stream_id}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Fatal error in processing thread for stream {self.stream_id}: {e}")
            self.processing_error.set()
            self.stop_flag.set()
        finally:
            logger.info(f"Processing thread exiting for stream {self.stream_id}...")
    
    def get_frame(self):
        try:
            frame = self.output_queue.get(timeout=0.1)
            return frame
        except queue.Empty:
            return None

# Global registry for stream processors
processors = {}
processor_lock = threading.Lock()

def get_processor(stream_id):
    with processor_lock:
        if stream_id not in processors:
            processors[stream_id] = StreamProcessor(stream_id)
        return processors[stream_id]

def remove_processor(stream_id):
    with processor_lock:
        if stream_id in processors:
            processor = processors[stream_id]
            processor.stop_stream()
            del processors[stream_id]
@app.route('/api/start_stream', methods=['POST'])
def start_stream():
    try:
        data = request.json
        stream_url = data.get('url')
        stream_id = data.get('stream_id', str(uuid.uuid4()))
        
        if not stream_url:
            return jsonify({'error': 'URL is required'}), 400
        
        processor = get_processor(stream_id)
        success = processor.start_stream(stream_url)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'Stream started from {stream_url}',
                'stream_url': stream_url,
                'stream_id': stream_id
            })
        else:
            return jsonify({'error': 'Failed to start stream'}), 500
            
    except Exception as e:
        logger.error(f"Error starting stream: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/video_feed/<stream_id>')
def video_feed(stream_id):
    with processor_lock:
        if stream_id not in processors:
            return jsonify({'error': 'Stream not found'}), 404
        processor = processors[stream_id]
    
    if not processor.is_streaming:
        return jsonify({'error': 'No active stream'}), 404
    
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
    
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/stop_stream', methods=['POST'])
def stop_stream():
    try:
        data = request.json
        stream_id = data.get('stream_id')
        
        if not stream_id:
            return jsonify({'error': 'stream_id is required'}), 400
        
        remove_processor(stream_id)
        return jsonify({
            'status': 'success',
            'message': f'Stream {stream_id} stopped'
        })
    except Exception as e:
        logger.error(f"Error stopping stream: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/list_streams')
def list_streams():
    with processor_lock:
        streams = []
        for stream_id, processor in processors.items():
            streams.append({
                'stream_id': stream_id,
                'stream_url': processor.stream_url,
                'is_streaming': processor.is_streaming,
            })
        return jsonify({'streams': streams})

@app.route('/api/stream_status/<stream_id>')
def stream_status(stream_id):
    with processor_lock:
        if stream_id not in processors:
            return jsonify({'error': 'Stream not found'}), 404
        processor = processors[stream_id]
    
    return jsonify({
        'stream_id': stream_id,
        'streaming': processor.is_streaming,
        'stream_url': processor.stream_url,
        'has_error': processor.processing_error.is_set()
    })

@app.route('/api/shared_stats')
def get_shared_stats():
    return jsonify(shared_tracker.get_stats())

@app.route('/api/get-suspicious-data')
def get_data():
    try:
        return jsonify({'status':'success','data': shared_tracker.get_suspicious_data()})
    except Exception as e:
        return jsonify({'status':'error','error':str(e)})

@app.route('/api/reload_db', methods=['POST'])
def reload_database():
    try:
        shared_tracker.load_embeddings_from_db()
        return jsonify({
            'status': 'success',
            'message': 'Database reloaded',
            'entries_loaded': len(shared_tracker.stored_labels)
        })
    except Exception as e:
        logger.error(f"Error reloading database: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/cleanup_faces', methods=['POST'])
def cleanup_faces():
    try:
        with shared_tracker.lock:
            before_count = len(shared_tracker.id2emb)
            shared_tracker.cleanup_old_faces()
            after_count = len(shared_tracker.id2emb)
        
        return jsonify({
            'status': 'success',
            'message': f'Cleaned up {before_count - after_count} old faces',
            'faces_before': before_count,
            'faces_after': after_count
        })
    except Exception as e:
        logger.error(f"Error cleaning up faces: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/consolidate_ids', methods=['POST'])
def consolidate_ids():
    try:
        with shared_tracker.lock:
            before_count = len(shared_tracker.id2emb)
            shared_tracker.consolidate_duplicate_ids()
            after_count = len(shared_tracker.id2emb)
        
        return jsonify({
            'status': 'success',
            'message': f'Consolidated {before_count - after_count} duplicate IDs',
            'faces_before': before_count,
            'faces_after': after_count
        })
    except Exception as e:
        logger.error(f"Error consolidating IDs: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        logger.info("Starting Multi-Stream Flask Face Tracking Backend with Shared Variables...")
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        with processor_lock:
            for processor in processors.values():
                processor.stop_stream()
    except Exception as e:
        logger.error(f"Error starting server: {e}")