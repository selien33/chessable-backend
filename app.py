# backend/app.py
from flask import Flask, request, jsonify, session, Response
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
import uuid
import hashlib
import os
from azure_storage import AzureTableStorage
from chess_logic import ChessGame
import threading
import time
import json

app = Flask(__name__)

app.secret_key = os.environ.get("SECRET_KEY")
#For cookies
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_NAME='session',
    SESSION_COOKIE_PARTITIONED=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=31)
)

# CORS configuration
CORS(app, 
    supports_credentials=True, 
    origins=["https://delightful-moss-0bc16db03.6.azurestaticapps.net"],
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "OPTIONS"]
)

# Update socketio configuration
socketio = SocketIO(app, 
    cors_allowed_origins=["https://delightful-moss-0bc16db03.6.azurestaticapps.net"],
    manage_session=False,
    async_mode='threading'
)
"""
CORS(app, supports_credentials=True, origins=[
    "https://delightful-moss-0bc16db03.6.azurestaticapps.net"
])
socketio = SocketIO(app, cors_allowed_origins="*")

socketio = SocketIO(app, cors_allowed_origins=[
    "https://delightful-moss-0bc16db03.6.azurestaticapps.net",
], manage_session=False)
"""
# Initialize Azure Table Storage
storage = None
try:
    storage = AzureTableStorage()
    print("Storage initialized successfully")
except Exception as e:
    print(f"!!!!!!!!!!!!!!!!!!!!!!!Storage initialization failed: {e}")
    print("!!!!!!!!!!!!!!!!!!!!!!!!Continuing without storage...")

# In-memory game state
waiting_players = []  # Track players waiting for a game
active_games = {}
player_heartbeats = {}  # Track player heartbeats

def hash_password(password):
    """Hash a password for storing."""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(stored_password, provided_password):
    """Verify a stored password against user provided password."""
    return stored_password == hash_password(provided_password)

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    
    # Check if username already exists
    existing_user = storage.query_entities('users', f"username eq '{username}'")
    if list(existing_user):
        return jsonify({'error': 'Username already exists'}), 400
    
    user_id = str(uuid.uuid4())
    user_data = {
        'PartitionKey': 'users',
        'RowKey': user_id,
        'username': username,
        'password': hash_password(password),
        'created_at': datetime.utcnow().isoformat(),
        'games_played': 0,
        'wins': 0
    }
    
    storage.insert_entity('users', user_data)
    return jsonify({'user_id': user_id, 'username': username})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    # Find user by username
    users = storage.query_entities('users', f"username eq '{username}'")
    user = next(iter(users), None)
    
    if not user or not verify_password(user['password'], password):
        return jsonify({'error': 'Invalid username or password'}), 401
    
    # Create session
    session['user_id'] = user['RowKey']
    session['username'] = user['username']
    
    return jsonify({
        'user_id': user['RowKey'],
        'username': user['username']
    })

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'message': 'Logged out successfully'})

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({
            'authenticated': True,
            'user_id': session['user_id'],
            'username': session['username']
        })
    return jsonify({'authenticated': False}), 401

@app.route('/api/games/history', methods=['GET'])
def get_game_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_id = session['user_id']
    
    # Get games where user was either white or black player
    games = []
    white_games = storage.query_entities('games', f"white_player eq '{user_id}' and status eq 'completed'")
    black_games = storage.query_entities('games', f"black_player eq '{user_id}' and status eq 'completed'")
    
    for game in white_games:
        games.append(game)
    for game in black_games:
        games.append(game)
    
    # Sort by completed_at date
    games.sort(key=lambda x: x.get('completed_at', ''), reverse=True)
    
    return jsonify({'games': games})

@app.route('/api/games/<game_id>', methods=['GET'])
def get_game(game_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    game = storage.get_entity('games', 'games', game_id)
    if not game:
        return jsonify({'error': 'Game not found'}), 404
    
    # Check if user was part of this game
    user_id = session['user_id']
    if game['white_player'] != user_id and game['black_player'] != user_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    return jsonify(game)

@app.route('/api/analyze', methods=['POST'])
def analyze_position():
    """Proxy request to Stockfish Container App for chess analysis"""
    import requests
    
    data = request.json
    fen = data.get('fen')
    depth = data.get('depth', 15)
    
    if not fen:
        return jsonify({'error': 'FEN position required'}), 400
    
    stockfish_url = os.environ.get('STOCKFISH_CONTAINER_URL')
    
    if not stockfish_url:
        return jsonify({'error': 'Stockfish service not configured'}), 500
    
    try:
        base_url = stockfish_url.rstrip('/')
        response = requests.post(
            f"{base_url}/evaluate", 
            json={'fen': fen, 'depth': depth},
            timeout=10
        )
        
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Stockfish service error: {str(e)}'}), 500

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")
    if 'user_id' not in session:
        return False  # Reject the connection if not authenticated
    emit('connection_established', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    sid = request.sid
    # Remove from waiting list if present
    global waiting_players
    waiting_players = [p for p in waiting_players if p['sid'] != sid]
    
    # Handle disconnection during game
    if sid in player_heartbeats:
        player_id = player_heartbeats[sid]['user_id']
        game_id = player_heartbeats[sid]['game_id']
        if game_id:
            handle_player_disconnect(game_id, player_id)

@socketio.on_error()
def error_handler(e):
    print(f"Socket error: {e}")


@socketio.on('join_waiting_list')
def handle_join_waiting():
    print(f"Join waiting list request from {request.sid}")
    print(f"Session data: {session}")
    
    if 'user_id' not in session:
        print(f"User not authenticated in session")
        emit('error', {'message': 'Not authenticated'})
        return False
    
    user_id = session['user_id']
    username = session['username']
    
    # Add to waiting list table
    waiting_entry = {
        'PartitionKey': 'waiting',
        'RowKey': user_id,
        'username': username,
        'sid': request.sid,
        'joined_at': datetime.utcnow().isoformat()
    }
    
    try:
        storage.insert_entity('waitinglist', waiting_entry)
    except:
        # Already in waiting list
        storage.update_entity('waitinglist', waiting_entry)
    
    emit('waiting_for_opponent')
    check_for_match()


@socketio.on('heartbeat')
def handle_heartbeat(data):
    game_id = data.get('game_id')
    
    if request.sid in player_heartbeats:
        player_heartbeats[request.sid]['last_heartbeat'] = datetime.utcnow()
        
        # Update game heartbeat in storage
        try:
            storage.update_entity('currentgames', {
                'PartitionKey': 'current',
                'RowKey': game_id,
                'last_heartbeat': datetime.utcnow().isoformat()
            })
        except:
            pass

@socketio.on('make_move')
def handle_move(data):
    game_id = data.get('game_id')
    move = data.get('move')
    
    if game_id not in active_games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = active_games[game_id]
    
    if not game.is_player_turn(session['user_id']):
        emit('error', {'message': 'Not your turn'})
        return
    
    if game.make_move(move):
        # Update game state in current games
        storage.update_entity('currentgames', {
            'PartitionKey': 'current',
            'RowKey': game_id,
            'state': game.get_fen(),
            'moves': game.get_moves_string()
        })
        
        # Get both players' sessions
        players_sids = []
        for sid, data in player_heartbeats.items():
            if data['game_id'] == game_id:
                players_sids.append(sid)
        
        # Emit to both players
        for sid in players_sids:
            socketio.emit('move_made', {
                'fen': game.get_fen(),
                'move': move,
                'turn': game.current_turn
            }, room=sid)
        
        # Check for game end
        if game.is_checkmate():
            winner = game.get_winner()
            end_game(game_id, winner, 'checkmate')
        elif game.is_stalemate():
            end_game(game_id, None, 'stalemate')
    else:
        emit('error', {'message': 'Invalid move'})

@socketio.on('abandon_game')
def handle_abandon(data):
    game_id = data.get('game_id')
    
    if game_id not in active_games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = active_games[game_id]
    abandoning_player = session['user_id']
    
    # Determine winner (opponent of abandoning player)
    winner = game.black_player if abandoning_player == game.white_player else game.white_player
    
    end_game(game_id, winner, 'abandon')

def handle_player_disconnect(game_id, player_id):
    """Handle player disconnection during game"""
    if game_id in active_games:
        game = active_games[game_id]
        winner = game.black_player if player_id == game.white_player else game.white_player
        end_game(game_id, winner, 'disconnect')

# Add this to your app.py
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = Response()
        res.headers['X-Content-Type-Options'] = '*'
        res.headers['Access-Control-Allow-Origin'] = 'https://delightful-moss-0bc16db03.6.azurestaticapps.net'
        res.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        res.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        res.headers['Access-Control-Allow-Credentials'] = 'true'
        return res


def check_for_match():
    """Check if we can match two players from the waiting list"""
    waiting = list(storage.query_entities('waitinglist', "PartitionKey eq 'waiting'"))
    
    if len(waiting) >= 2:
        # Sort by joined_at to ensure FIFO
        waiting.sort(key=lambda x: x['joined_at'])
        
        player1 = waiting[0]
        player2 = waiting[1]
        
        # Remove both from waiting list
        storage.delete_entity('waitinglist', 'waiting', player1['RowKey'])
        storage.delete_entity('waitinglist', 'waiting', player2['RowKey'])
        
        # Create game
        create_game(player1, player2)

def create_game(player1, player2):
    """Create a new game between two players"""
    game_id = str(uuid.uuid4())
    
    # Create game instance
    game = ChessGame(game_id, player1['RowKey'], player2['RowKey'])
    active_games[game_id] = game
    
    # Store in currentgames table
    game_data = {
        'PartitionKey': 'current',
        'RowKey': game_id,
        'white_player': player1['RowKey'],
        'black_player': player2['RowKey'],
        'white_username': player1['username'],
        'black_username': player2['username'],
        'state': game.get_fen(),
        'moves': '',
        'created_at': datetime.utcnow().isoformat(),
        'status': 'active',
        'last_heartbeat': datetime.utcnow().isoformat()
    }
    
    storage.insert_entity('currentgames', game_data)
    
    # Initialize heartbeats
    player_heartbeats[player1['sid']] = {
        'user_id': player1['RowKey'],
        'game_id': game_id,
        'last_heartbeat': datetime.utcnow()
    }
    player_heartbeats[player2['sid']] = {
        'user_id': player2['RowKey'],
        'game_id': game_id,
        'last_heartbeat': datetime.utcnow()
    }
    
    # Notify both players
    socketio.emit('game_started', {
        'game_id': game_id,
        'white': {'id': player1['RowKey'], 'username': player1['username']},
        'black': {'id': player2['RowKey'], 'username': player2['username']},
        'fen': game.get_fen()
    }, room=player1['sid'])
    
    socketio.emit('game_started', {
        'game_id': game_id,
        'white': {'id': player1['RowKey'], 'username': player1['username']},
        'black': {'id': player2['RowKey'], 'username': player2['username']},
        'fen': game.get_fen()
    }, room=player2['sid'])


def end_game(game_id, winner, reason):
    """End a game and update all necessary tables"""
    if game_id not in active_games:
        return
    
    game = active_games[game_id]
    
    # Move from currentgames to games table
    current_game = storage.get_entity('currentgames', 'current', game_id)
    
    # Create completed game entry
    completed_game = {
        'PartitionKey': 'games',
        'RowKey': game_id,
        'white_player': game.white_player,
        'black_player': game.black_player,
        'white_username': current_game['white_username'],
        'black_username': current_game['black_username'],
        'state': game.get_fen(),
        'moves': game.get_moves_string(),
        'created_at': current_game['created_at'],
        'completed_at': datetime.utcnow().isoformat(),
        'status': 'completed',
        'winner': winner,
        'end_reason': reason
    }
    
    storage.insert_entity('games', completed_game)
    
    # Delete from current games
    storage.delete_entity('currentgames', 'current', game_id)
    
    # Update player statistics
    if winner:
        winner_data = storage.get_entity('users', 'users', winner)
        winner_data['wins'] += 1
        winner_data['games_played'] += 1
        storage.update_entity('users', winner_data)
        
        loser = game.white_player if winner == game.black_player else game.black_player
        loser_data = storage.get_entity('users', 'users', loser)
        loser_data['games_played'] += 1
        storage.update_entity('users', loser_data)
    else:
        # Draw
        for player_id in [game.white_player, game.black_player]:
            player_data = storage.get_entity('users', 'users', player_id)
            player_data['games_played'] += 1
            storage.update_entity('users', player_data)
    
    # Notify players
    players_sids = []
    for sid, data in player_heartbeats.items():
        if data['game_id'] == game_id:
            players_sids.append(sid)
    
    for sid in players_sids:
        socketio.emit('game_over', {
            'winner': winner,
            'reason': reason
        }, room=sid)
    
    # Clean up
    del active_games[game_id]
    for sid, data in list(player_heartbeats.items()):
        if data['game_id'] == game_id:
            del player_heartbeats[sid]

# Heartbeat checker thread
def check_heartbeats():
    while True:
        time.sleep(5)  # Check every 5 seconds
        current_time = datetime.utcnow()
        
        for sid, data in list(player_heartbeats.items()):
            last_heartbeat = data['last_heartbeat']
            if (current_time - last_heartbeat).total_seconds() > 10:  # 10 seconds timeout
                # Player is disconnected
                game_id = data['game_id']
                user_id = data['user_id']
                handle_player_disconnect(game_id, user_id)

# Start heartbeat checker
heartbeat_thread = threading.Thread(target=check_heartbeats, daemon=True)
heartbeat_thread.start()

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "backend"})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)