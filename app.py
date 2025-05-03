# backend/app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import uuid
from azure_storage import AzureTableStorage
from chess_logic import ChessGame

app = Flask(__name__)
"""CORS(app, resources={
    r"/*": {
        "origins": [
            "https://delightful-moss-0bc16db03.6.azurestaticapps.net",
            "http://localhost:5173"
        ]
    }
})
socketio = SocketIO(app, cors_allowed_origins=[
    "https://delightful-moss-0bc16db03.6.azurestaticapps.net",
    "http://localhost:5173"
])"""
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*") # less secure but may work

# Initialize Azure Table Storage
# storage = AzureTableStorage()
# Initialize Azure Table Storage
storage = None
try:
    storage = AzureTableStorage()
    print("Storage initialized successfully")
except Exception as e:
    print(f"Storage initialization failed: {e}")
    print("Continuing without storage...")

# In-memory game state
active_games = {}
waiting_players = []

@app.route('/api/register', methods=['POST'])
def register():
    #debugging
    print("Register endpoint called")
    data = request.json
    username = data.get('username')
    
    if not username:
        return jsonify({'error': 'Username required'}), 400
    
    user_id = str(uuid.uuid4())
    
    if storage:
        try:
            user_data = {
                'PartitionKey': 'users',
                'RowKey': user_id,
                'username': username,
                'created_at': datetime.utcnow().isoformat(),
                'games_played': 0,
                'wins': 0
            }
            storage.insert_entity('users', user_data)
        except Exception as e:
            print(f"Storage error: {e}")
    else:
        print(f"Registered user {username} without storage")
    
    return jsonify({'user_id': user_id, 'username': username})
    """data = request.json
    username = data.get('username')
    
    if not username:
        return jsonify({'error': 'Username required'}), 400
    
    user_id = str(uuid.uuid4())
    user_data = {
        'PartitionKey': 'users',
        'RowKey': user_id,
        'username': username,
        'created_at': datetime.utcnow().isoformat(),
        'games_played': 0,
        'wins': 0
    }
    
    storage.insert_entity('users', user_data)
    return jsonify({'user_id': user_id, 'username': username})"""

@app.route('/api/user/<user_id>', methods=['GET'])
def get_user(user_id):
    user = storage.get_entity('users', 'users', user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    return jsonify(user)

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('join_game')
def handle_join_game(data):
    user_id = data.get('user_id')
    username = data.get('username')
    
    if not user_id or not username:
        emit('error', {'message': 'Invalid user data'})
        return
    
    # Add player to waiting list or match with existing player
    if waiting_players:
        opponent = waiting_players.pop(0)
        game_id = str(uuid.uuid4())
        
        # Create new game
        game = ChessGame(game_id, user_id, opponent['user_id'])
        active_games[game_id] = game
        
        # Store game in Azure Table
        game_data = {
            'PartitionKey': 'games',
            'RowKey': game_id,
            'white_player': user_id,
            'black_player': opponent['user_id'],
            'state': game.get_fen(),
            'moves': '',
            'created_at': datetime.utcnow().isoformat(),
            'status': 'active'
        }
        storage.insert_entity('games', game_data)
        
        # Notify both players
        join_room(game_id)
        join_room(game_id, sid=opponent['sid'])
        
        emit('game_started', {
            'game_id': game_id,
            'white': {'id': user_id, 'username': username},
            'black': {'id': opponent['user_id'], 'username': opponent['username']},
            'fen': game.get_fen()
        }, room=game_id)
    else:
        waiting_players.append({
            'user_id': user_id,
            'username': username,
            'sid': request.sid
        })
        emit('waiting_for_opponent')

@socketio.on('make_move')
def handle_move(data):
    game_id = data.get('game_id')
    user_id = data.get('user_id')
    move = data.get('move')
    
    if game_id not in active_games:
        emit('error', {'message': 'Game not found'})
        return
    
    game = active_games[game_id]
    
    if not game.is_player_turn(user_id):
        emit('error', {'message': 'Not your turn'})
        return
    
    if game.make_move(move):
        # Update game state in Azure Table
        storage.update_entity('games', {
            'PartitionKey': 'games',
            'RowKey': game_id,
            'state': game.get_fen(),
            'moves': game.get_moves_string()
        })
        
        emit('move_made', {
            'fen': game.get_fen(),
            'move': move,
            'turn': game.current_turn
        }, room=game_id)
        
        # Check for game end
        if game.is_checkmate():
            winner = game.get_winner()
            emit('game_over', {'winner': winner, 'reason': 'checkmate'}, room=game_id)
            update_game_result(game_id, winner)
        elif game.is_stalemate():
            emit('game_over', {'winner': None, 'reason': 'stalemate'}, room=game_id)
            update_game_result(game_id, None)
    else:
        emit('error', {'message': 'Invalid move'})


@app.route('/api/evaluate', methods=['POST'])
def evaluate_position():
    """Proxy request to Stockfish Container App for chess evaluation"""
    import requests
    import os
    
    data = request.json
    fen = data.get('fen')
    depth = data.get('depth', 15)
    
    if not fen:
        return jsonify({'error': 'FEN position required'}), 400
    
    # Get the Container App URL from environment variable
    stockfish_url = os.environ.get('STOCKFISH_CONTAINER_URL')
    
    if not stockfish_url:
        return jsonify({'error': 'Stockfish service not configured'}), 500
    
    try:
        # Ensure the URL doesn't have trailing slash and add the endpoint
        base_url = stockfish_url.rstrip('/')
        response = requests.post(
            f"{base_url}/evaluate", 
            json={'fen': fen, 'depth': depth},
            timeout=10  # Add timeout to prevent hanging
        )
        
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Stockfish service error: {str(e)}'}), 500
    
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "backend"})

def update_game_result(game_id, winner):
    """Update game result and player statistics"""
    game = active_games[game_id]
    
    # Update game status
    storage.update_entity('games', {
        'PartitionKey': 'games',
        'RowKey': game_id,
        'status': 'completed',
        'winner': winner,
        'completed_at': datetime.utcnow().isoformat()
    })
    
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
    
    # Remove game from active games
    del active_games[game_id]


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)