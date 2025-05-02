# backend/chess_logic.py
import chess

class ChessGame:
    def __init__(self, game_id, white_player, black_player):
        self.game_id = game_id
        self.white_player = white_player
        self.black_player = black_player
        self.board = chess.Board()
        self.moves = []
        self.current_turn = 'white'
    
    def is_player_turn(self, player_id):
        if self.current_turn == 'white' and player_id == self.white_player:
            return True
        if self.current_turn == 'black' and player_id == self.black_player:
            return True
        return False
    
    def make_move(self, move_uci):
        try:
            move = chess.Move.from_uci(move_uci)
            if move in self.board.legal_moves:
                self.board.push(move)
                self.moves.append(move_uci)
                self.current_turn = 'black' if self.current_turn == 'white' else 'white'
                return True
        except ValueError:
            return False
        return False
    
    def get_fen(self):
        return self.board.fen()
    
    def get_moves_string(self):
        return ','.join(self.moves)
    
    def is_checkmate(self):
        return self.board.is_checkmate()
    
    def is_stalemate(self):
        return self.board.is_stalemate()
    
    def get_winner(self):
        if self.board.is_checkmate():
            return self.black_player if self.current_turn == 'white' else self.white_player
        return None

