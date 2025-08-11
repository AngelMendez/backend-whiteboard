# main.py
import os
import uuid
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
import google.cloud.firestore
from dotenv import load_dotenv
import datetime
from fastapi import File, UploadFile
from google.cloud import storage
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
import json
import secrets

# --- Configuración Inicial ---
load_dotenv()

# Initialize Google Cloud clients with secure authentication
db = None
storage_client = None

def initialize_google_clients():
    """Initialize Google Cloud clients with retry logic"""
    global db, storage_client
    try:
        # Use Google Cloud's default authentication (recommended for Cloud Run)
        # This will automatically use the service account attached to the Cloud Run service
        print("🔐 Attempting to initialize Google Cloud clients...")
        
        # Explicitly set the project ID and use default credentials
        project_id = "whiteboard-foundamental"
        
        # Force the use of Application Default Credentials
        os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
        print("🔧 Cleared GOOGLE_APPLICATION_CREDENTIALS environment variable")
        
        # Initialize Firestore client with explicit project
        db = google.cloud.firestore.Client(project=project_id)
        print("✅ Firestore client initialized successfully")
        
        # Initialize Storage client with explicit project
        storage_client = storage.Client(project=project_id)
        print("✅ Storage client initialized successfully")
        
        return True
    except Exception as e:
        print(f"❌ Error initializing Google Cloud clients: {e}")
        print("⚠️  Will retry on first request")
        return False

# Try to initialize clients at startup
initialize_google_clients()

BUCKET_NAME = "whiteboard-bucket"

app = FastAPI()

# CORS configuration - ALLOW ANY ORIGIN FOR TESTING
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow any origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# VERY OBVIOUS CHANGE TO VERIFY UPDATE IS WORKING - ADD THIS LINE
print("🚀 BACKEND UPDATED SUCCESSFULLY - CORS NOW ALLOWS ANY ORIGIN! 🚀")
print("🆔 UNIQUE ID: 2025-08-10-13-52-50-UPDATE-COMPLETE 🆔")
print("🔧 FIXED: Google Cloud project explicitly set to whiteboard-foundamental 🔧")
print("💬 FIXED: Chat messaging and file upload now properly handle usernames 💬")

# --- Gestor de Conexiones WebSocket ---

class ConnectionManager:
    def __init__(self):
        # Guardaremos las conexiones activas por sesión/sala
        self.active_connections: dict[str, list[WebSocket]] = {}
        # User information for each connection
        self.user_info: dict[WebSocket, dict] = {}

    async def connect(self, websocket: WebSocket, session_id: str, user_id: str = None, username: str = None):
        print(f"🔌 New WebSocket connection attempt for session: {session_id}")
        await websocket.accept()
        print(f"✅ WebSocket accepted for session: {session_id}")
        
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append(websocket)
        
        # For now, we'll generate a temporary user info
        # The actual username will come from the chat messages
        if not user_id:
            user_id = str(uuid.uuid4())
        if not username:
            username = f"User_{user_id[:8]}"
        
        self.user_info[websocket] = {
            'user_id': user_id,
            'username': username,
            'session_id': session_id,
            'connected_at': datetime.datetime.now(datetime.timezone.utc)
        }
        
        print(f"👤 User {username} connected to session {session_id}")
        
        # Broadcast user joined (but don't wait for it to complete)
        try:
            await self.broadcast_user_presence(session_id, 'joined', username)
            print(f"📢 User joined broadcast sent for {username}")
        except Exception as e:
            print(f"⚠️ Warning: Failed to broadcast user joined: {e}")

    def disconnect(self, websocket: WebSocket, session_id: str):
        username = 'Unknown'
        
        # Get user info before removing
        if websocket in self.user_info:
            user_info = self.user_info[websocket]
            username = user_info.get('username', 'Unknown')
            # Remove user info
            del self.user_info[websocket]
        
        # Remove from active connections
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
            
            # Clean up empty sessions
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]
        
        return username

    async def broadcast(self, message: str, session_id: str, exclude_websocket: WebSocket = None):
        print(f"📢 Broadcasting message to session {session_id}")
        if session_id in self.active_connections:
            print(f"👥 Found {len(self.active_connections[session_id])} active connections")
            # Create a copy of the list to avoid modification during iteration
            connections_to_remove = []
            for connection in self.active_connections[session_id]:
                if connection != exclude_websocket:
                    try:
                        # Try to send the message - if it fails, the connection is closed
                        await connection.send_text(message)
                        print(f"✅ Message sent to connection")
                    except Exception as e:
                        print(f"⚠️ Warning: Failed to send message to connection: {e}")
                        # Mark connection for removal if sending fails
                        connections_to_remove.append(connection)
            
            # Remove closed connections
            for connection in connections_to_remove:
                if connection in self.active_connections[session_id]:
                    self.active_connections[session_id].remove(connection)
                    print(f"🗑️ Removed closed connection")
                if connection in self.user_info:
                    del self.user_info[connection]
                    print(f"🗑️ Removed user info for closed connection")
        else:
            print(f"❌ No active connections found for session {session_id}")

    async def broadcast_user_presence(self, session_id: str, action: str, username: str):
        presence_message = {
            'type': 'presence',
            'action': action,
            'username': username,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        await self.broadcast(json.dumps(presence_message), session_id)

    def get_active_users(self, session_id: str) -> list:
        users = []
        if session_id in self.active_connections:
            for connection in self.active_connections[session_id]:
                if connection in self.user_info:
                    users.append(self.user_info[connection]['username'])
        return users

manager = ConnectionManager()

# --- Endpoints ---

@app.get("/")
def read_root():
    return {"Hello": "Backend is running"}

@app.websocket("/ws/chat/{session_id}")
async def websocket_chat_endpoint(websocket: WebSocket, session_id: str):
    print(f"🔌 Chat WebSocket endpoint called for session: {session_id}")
    await manager.connect(websocket, session_id)
    
    # Send current active users to the new connection
    active_users = manager.get_active_users(session_id)
    users_message = {
        'type': 'users_list',
        'users': active_users
    }
    print(f"👥 Sending users list to new connection: {active_users}")
    await websocket.send_text(json.dumps(users_message))
    
    try:
        while True:
            data = await websocket.receive_text()
            print(f"📨 Received chat message: {data}")
            
            # Parse the message to check if it's a structured message
            try:
                message_data = json.loads(data)
                if message_data.get('type') == 'chat':
                    text = message_data.get('text', '')
                    # Use username from the message, fallback to connection user info
                    username = message_data.get('username') or manager.user_info.get(websocket, {}).get('username', 'Unknown')
                    
                    print(f"💬 Processing chat message from {username}: {text}")
                    
                    # Create structured message
                    chat_message = {
                        'type': 'chat',
                        'text': text,
                        'username': username,
                        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
                    }
                    
                    # Save to Firestore if available
                    if db:
                        try:
                            chat_ref = db.collection('chats').document(session_id).collection('messages')
                            await run_in_threadpool(chat_ref.add, chat_message)
                        except Exception as e:
                            print(f"⚠️ Warning: Failed to save chat message to Firestore: {e}")
                    else:
                        print("⚠️ Warning: Firestore not available, skipping chat message save")
                    
                    # Broadcast to all
                    print(f"📢 Broadcasting chat message to session {session_id}")
                    await manager.broadcast(json.dumps(chat_message), session_id)
                elif message_data.get('type') == 'clear':
                    # Handle clear canvas command
                    clear_message = {
                        'type': 'clear_canvas',
                        'username': manager.user_info.get(websocket, {}).get('username', 'Unknown')
                    }
                    await manager.broadcast(json.dumps(clear_message), session_id)
            except json.JSONDecodeError:
                # Handle plain text messages (backward compatibility)
                user_info = manager.user_info.get(websocket, {})
                username = user_info.get('username', 'Unknown')
                
                print(f"💬 Processing legacy chat message from {username}: {data}")
                
                chat_message = {
                    'type': 'chat',
                    'text': data,
                    'username': username,
                    'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
                
                # Save to Firestore if available
                if db:
                    try:
                        chat_ref = db.collection('chats').document(session_id).collection('messages')
                        await run_in_threadpool(chat_ref.add, chat_message)
                    except Exception as e:
                        print(f"⚠️ Warning: Failed to save legacy chat message to Firestore: {e}")
                else:
                    print("⚠️ Warning: Firestore not available, skipping legacy chat message save")
                
                # Broadcast to all
                print(f"📢 Broadcasting legacy chat message to session {session_id}")
                await manager.broadcast(json.dumps(chat_message), session_id)
                
    except WebSocketDisconnect:
        print(f"❌ Chat WebSocket disconnected for session: {session_id}")
        username = manager.disconnect(websocket, session_id)
        try:
            await manager.broadcast_user_presence(session_id, 'left', username)
        except Exception as e:
            print(f"⚠️ Warning: Failed to broadcast user left message: {e}")
    except Exception as e:
        print(f"❌ Error in chat WebSocket: {e}")
        username = manager.disconnect(websocket, session_id)

# Endpoint WebSocket para el WHITEBOARD
@app.websocket("/ws/whiteboard/{session_id}")
async def websocket_whiteboard_endpoint(websocket: WebSocket, session_id: str):
    await manager.connect(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Parse to check if it's a clear command
            try:
                message_data = json.loads(data)
                if message_data.get('type') == 'clear_canvas':
                    # Broadcast clear command to all whiteboard connections
                    await manager.broadcast(data, session_id)
                else:
                    # Regular drawing data
                    await manager.broadcast(data, session_id)
            except json.JSONDecodeError:
                # Regular drawing data (not JSON)
                await manager.broadcast(data, session_id)
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
    except Exception as e:
        print(f"❌ Error in whiteboard WebSocket: {e}")
        manager.disconnect(websocket, session_id)

@app.post("/uploadfile/{session_id}")
async def create_upload_file(session_id: str, file: UploadFile = File(...), username: str = Form("Unknown")):
    print(f"📁 File upload request for session: {session_id}, username: {username}")
    print(f"📁 File details: {file.filename}, {file.size} bytes, {file.content_type}")
    
    if not file:
        print("❌ No file sent")
        return {"error": "No file sent"}

    # Try to initialize clients if they're not available
    global storage_client, db
    if not storage_client or not db:
        print("🔄 Attempting to initialize Google Cloud clients...")
        if initialize_google_clients():
            print("✅ Google Cloud clients initialized successfully")
        else:
            print("❌ Failed to initialize Google Cloud clients")
            return {"error": "Storage service not available"}

    # Check if storage client is available
    if not storage_client:
        print("❌ Storage service not available")
        return {"error": "Storage service not available"}

    try:
        # Generate secure filename
        file_extension = os.path.splitext(file.filename)[1]
        secure_filename = f"{secrets.token_urlsafe(16)}{file_extension}"
        print(f"🔐 Generated secure filename: {secure_filename}")
        
        # Upload file to GCS with secure name
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(secure_filename)

        print(f"☁️ Uploading to Google Cloud Storage...")
        blob.upload_from_file(file.file, content_type=file.content_type)
        print(f"✅ File uploaded to GCS successfully")

        # Generate secure download URL (expires in 1 hour)
        try:
            download_url = blob.generate_signed_url(
                version="v4",
                expiration=datetime.timedelta(hours=1),
                method="GET"
            )
            print(f"🔗 Generated signed download URL")
        except Exception as signed_url_error:
            print(f"⚠️ Warning: Failed to generate signed URL: {signed_url_error}")
            print(f"🔗 Using public URL instead for local development")
            # For local development, use public URL if signed URL fails
            download_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{secure_filename}"
            # Make the blob publicly readable temporarily
            try:
                blob.make_public()
                print(f"✅ Made blob publicly readable")
            except Exception as public_error:
                print(f"⚠️ Warning: Failed to make blob public: {public_error}")
                # If we can't make it public, we'll still return the URL but it might not work
                download_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{secure_filename}"

        # Get file info for preview
        file_info = {
            'original_name': file.filename,
            'secure_name': secure_filename,
            'size': file.size,
            'content_type': file.content_type,
            'download_url': download_url,
            'uploaded_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        # Create file message
        file_message = {
            'type': 'file_shared',
            'file_info': file_info,
            'username': username,  # Use the username from form data
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        print(f"💬 Created file message: {file_message}")

        # Save to Firestore if available
        if db:
            try:
                chat_ref = db.collection('chats').document(session_id).collection('messages')
                await run_in_threadpool(chat_ref.add, file_message)
                print(f"💾 File message saved to Firestore")
            except Exception as e:
                print(f"⚠️ Warning: Failed to save to Firestore: {e}")
        else:
            print("⚠️ Warning: Firestore not available, skipping save")

        # Notify everyone in the chat
        print(f"📢 Broadcasting file message to session {session_id}")
        await manager.broadcast(json.dumps(file_message), session_id)

        print(f"✅ File upload completed successfully")
        return file_info
        
    except Exception as e:
        print(f"❌ Error uploading file: {e}")
        return {"error": f"File upload failed: {str(e)}"}

@app.get("/download/{secure_filename}")
async def download_file(secure_filename: str):
    """Secure file download endpoint"""
    # Try to initialize clients if they're not available
    global storage_client
    if not storage_client:
        print("🔄 Attempting to initialize Google Cloud clients...")
        if initialize_google_clients():
            print("✅ Google Cloud clients initialized successfully")
        else:
            print("❌ Failed to initialize Google Cloud clients")
            return {"error": "Storage service not available"}
    
    if not storage_client:
        return {"error": "Storage service not available"}
        
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(secure_filename)
        
        # Generate a new signed URL for download
        try:
            download_url = blob.generate_signed_url(
                version="v4",
                expiration=datetime.timedelta(hours=1),
                method="GET"
            )
        except Exception as signed_url_error:
            print(f"⚠️ Warning: Failed to generate signed download URL: {signed_url_error}")
            print(f"🔗 Using public URL instead for local development")
            # For local development, use public URL if signed URL fails
            download_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{secure_filename}"
        
        return {"download_url": download_url}
    except Exception as e:
        print(f"❌ Error generating download URL: {e}")
        return {"error": "File not found or access denied"}