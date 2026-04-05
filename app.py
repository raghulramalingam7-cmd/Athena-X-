from flask import (Flask, request, jsonify, render_template,
                   session, redirect, url_for, flash)
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, json, time, hashlib, secrets, re
from functools import wraps
from datetime import timedelta

app = Flask(__name__)

# ── SECURITY CONFIGURATION ────────────────────────────────────────────────────
# Secret key: reads from environment variable or generates a stable one from a
# machine-specific seed stored in a local file (never hardcoded).
_KEY_FILE = os.path.join(os.path.dirname(__file__), '.secret_key')
def _load_secret_key():
    env_key = os.environ.get('ATHENA_SECRET_KEY')
    if env_key:
        return env_key
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, 'rb') as f:
            return f.read()
    key = secrets.token_bytes(32)
    with open(_KEY_FILE, 'wb') as f:
        f.write(key)
    os.chmod(_KEY_FILE, 0o600)
    return key

app.secret_key = _load_secret_key()

# Session cookie hardening — Secure flag is set based on environment.
# Set ATHENA_HTTPS=0 to disable Secure flag when running over plain HTTP (dev/local).
_https_mode = os.environ.get('ATHENA_HTTPS', '1') != '0'
app.config.update(
    SESSION_COOKIE_HTTPONLY   = True,         # JS cannot read the cookie
    SESSION_COOKIE_SAMESITE   = 'Lax',        # CSRF mitigation
    SESSION_COOKIE_SECURE     = _https_mode,  # True in production (HTTPS), False on HTTP
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8),  # auto-logout after 8 h
)

# ── RATE-LIMIT STORE (in-memory, per-IP) ─────────────────────────────────────
# Tracks failed login attempts: { ip -> [timestamp, ...] }
_LOGIN_ATTEMPTS: dict = {}
_MAX_ATTEMPTS   = 5          # max failures before lockout
_WINDOW_SECONDS = 300        # 5-minute sliding window
_LOCKOUT_SECONDS= 600        # 10-minute lockout after exceeding limit

def _get_ip():
    """Return the real client IP (supports X-Forwarded-For from proxies)."""
    return (request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown').split(',')[0].strip()

def _is_rate_limited(ip: str) -> tuple[bool, int]:
    """Return (is_locked, seconds_remaining)."""
    now = time.time()
    attempts = _LOGIN_ATTEMPTS.get(ip, [])
    # Keep only attempts inside the window
    attempts = [t for t in attempts if now - t < _WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[ip] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        oldest = attempts[0]
        wait = int(_LOCKOUT_SECONDS - (now - oldest))
        if wait > 0:
            return True, wait
        # Lockout expired — reset
        _LOGIN_ATTEMPTS[ip] = []
    return False, 0

def _record_failed_login(ip: str):
    _LOGIN_ATTEMPTS.setdefault(ip, []).append(time.time())

def _clear_failed_logins(ip: str):
    _LOGIN_ATTEMPTS.pop(ip, None)

DB_PATH = os.path.join(os.path.dirname(__file__), 'gully_sports.db')



# ── SECURITY HEADERS ─────────────────────────────────────────────────────────
@app.after_request
def add_security_headers(resp):
    # Basic hardening
    resp.headers['X-Frame-Options']        = 'SAMEORIGIN'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-XSS-Protection']       = '1; mode=block'
    resp.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    # HTTPS-specific: tell browsers to always use HTTPS for 1 year
    resp.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    # Permissions policy — disable unnecessary browser features
    resp.headers['Permissions-Policy']     = 'geolocation=(), microphone=(), camera=()'
    return resp


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT, phone TEXT,
                role TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, icon TEXT DEFAULT '🏆',
                description TEXT, is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport_id INTEGER,
                title TEXT NOT NULL,
                team1 TEXT NOT NULL, team2 TEXT NOT NULL,
                venue TEXT, event_date TEXT, event_time TEXT,
                description TEXT,
                max_registrations INTEGER DEFAULT 0,
                total_overs INTEGER DEFAULT 6,
                status TEXT DEFAULT 'upcoming',
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(sport_id) REFERENCES sports(id)
            );
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, event_id INTEGER,
                team_preference TEXT, position TEXT, notes TEXT,
                status TEXT DEFAULT 'pending',
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(event_id) REFERENCES events(id),
                UNIQUE(user_id, event_id)
            );

            -- CRICKET
            CREATE TABLE IF NOT EXISTS cricket_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                total_overs INTEGER DEFAULT 6,
                toss_winner TEXT, batting_first TEXT,
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS cricket_innings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, inning_no INTEGER,
                batting_team TEXT, bowling_team TEXT,
                total_runs INTEGER DEFAULT 0, wickets INTEGER DEFAULT 0,
                balls INTEGER DEFAULT 0, extras INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                FOREIGN KEY(match_id) REFERENCES cricket_matches(id)
            );
            CREATE TABLE IF NOT EXISTS cricket_batting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inning_id INTEGER, player_name TEXT,
                runs INTEGER DEFAULT 0, balls INTEGER DEFAULT 0,
                fours INTEGER DEFAULT 0, sixes INTEGER DEFAULT 0,
                is_out INTEGER DEFAULT 0,
                out_type TEXT, bowler TEXT, fielder TEXT,
                is_on_strike INTEGER DEFAULT 0,
                batting_order INTEGER DEFAULT 0,
                FOREIGN KEY(inning_id) REFERENCES cricket_innings(id)
            );
            CREATE TABLE IF NOT EXISTS cricket_bowling (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inning_id INTEGER, player_name TEXT,
                balls INTEGER DEFAULT 0, runs INTEGER DEFAULT 0,
                wickets INTEGER DEFAULT 0, maidens INTEGER DEFAULT 0,
                current_over_runs INTEGER DEFAULT 0,
                current_over_balls INTEGER DEFAULT 0,
                FOREIGN KEY(inning_id) REFERENCES cricket_innings(id)
            );
            CREATE TABLE IF NOT EXISTS cricket_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inning_id INTEGER, over_no INTEGER, ball_no INTEGER,
                batsman TEXT, bowler TEXT,
                runs INTEGER DEFAULT 0, extra_type TEXT,
                extra_runs INTEGER DEFAULT 0,
                is_wicket INTEGER DEFAULT 0,
                wicket_type TEXT, fielder TEXT
            );
            CREATE TABLE IF NOT EXISTS cricket_players (
                match_id INTEGER, team TEXT, players TEXT
            );
            CREATE TABLE IF NOT EXISTS cricket_undo_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inning_id INTEGER,
                delivery_id INTEGER,
                innings_snapshot TEXT,
                batting_snapshot TEXT,
                bowling_snapshot TEXT
            );
            CREATE TABLE IF NOT EXISTS cricket_event_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                team TEXT,
                player_name TEXT,
                role TEXT DEFAULT 'batsman',
                is_impact INTEGER DEFAULT 0,
                player_order INTEGER DEFAULT 0,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS event_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                team TEXT,
                player_name TEXT,
                role TEXT DEFAULT 'player',
                is_sub INTEGER DEFAULT 0,
                player_order INTEGER DEFAULT 0,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );

            -- KABADDI
            CREATE TABLE IF NOT EXISTS kabaddi_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                current_half INTEGER DEFAULT 1,
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS kabaddi_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT, player_no INTEGER DEFAULT 0,
                raid_points INTEGER DEFAULT 0,
                tackle_points INTEGER DEFAULT 0,
                super_tackles INTEGER DEFAULT 0,
                bonus_points INTEGER DEFAULT 0,
                is_out INTEGER DEFAULT 0,
                revivals INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES kabaddi_matches(id)
            );
            CREATE TABLE IF NOT EXISTS kabaddi_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, half_no INTEGER DEFAULT 1,
                event_type TEXT,
                raiding_team TEXT, defending_team TEXT,
                raider_name TEXT,
                points_raiding INTEGER DEFAULT 0,
                points_defending INTEGER DEFAULT 0,
                is_all_out INTEGER DEFAULT 0,
                is_super_tackle INTEGER DEFAULT 0,
                is_bonus INTEGER DEFAULT 0,
                is_super_raid INTEGER DEFAULT 0,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES kabaddi_matches(id)
            );
            CREATE TABLE IF NOT EXISTS kabaddi_out_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER, player_name TEXT, team TEXT
            );

            -- FOOTBALL
            CREATE TABLE IF NOT EXISTS football_penalty_shootout (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                status TEXT DEFAULT 'active',
                winner TEXT,
                round_no INTEGER DEFAULT 1,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES football_matches(id)
            );
            CREATE TABLE IF NOT EXISTS football_penalty_kicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shootout_id INTEGER,
                round_no INTEGER DEFAULT 1,
                team TEXT,
                player_name TEXT,
                kick_order INTEGER DEFAULT 1,
                result TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(shootout_id) REFERENCES football_penalty_shootout(id)
            );
            CREATE TABLE IF NOT EXISTS football_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                current_half INTEGER DEFAULT 1,
                status TEXT DEFAULT 'setup', result TEXT,
                half_duration INTEGER DEFAULT 45,
                extra_time_1 INTEGER DEFAULT 0,
                extra_time_2 INTEGER DEFAULT 0,
                timer_started_at REAL DEFAULT 0,
                timer_offset INTEGER DEFAULT 0,
                timer_running INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS football_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT, player_no INTEGER DEFAULT 0,
                goals INTEGER DEFAULT 0, assists INTEGER DEFAULT 0,
                yellow_cards INTEGER DEFAULT 0, red_cards INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_sub INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES football_matches(id)
            );
            CREATE TABLE IF NOT EXISTS football_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, event_type TEXT,
                team TEXT, player_name TEXT, assist_player TEXT,
                minute INTEGER DEFAULT 0, half INTEGER DEFAULT 1, note TEXT,
                timer_second INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES football_matches(id)
            );

            -- BASKETBALL
            CREATE TABLE IF NOT EXISTS basketball_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                current_quarter INTEGER DEFAULT 1,
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS basketball_quarters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, quarter_no INTEGER,
                team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
                team1_fouls INTEGER DEFAULT 0, team2_fouls INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES basketball_matches(id)
            );
            CREATE TABLE IF NOT EXISTS basketball_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT, player_no INTEGER DEFAULT 0,
                points INTEGER DEFAULT 0, rebounds INTEGER DEFAULT 0,
                assists INTEGER DEFAULT 0, steals INTEGER DEFAULT 0,
                blocks INTEGER DEFAULT 0, fouls INTEGER DEFAULT 0,
                technical_fouls INTEGER DEFAULT 0,
                is_fouled_out INTEGER DEFAULT 0,
                is_ejected INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES basketball_matches(id)
            );
            CREATE TABLE IF NOT EXISTS basketball_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, event_type TEXT,
                team TEXT, player_name TEXT,
                points INTEGER DEFAULT 0, quarter INTEGER DEFAULT 1, note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES basketball_matches(id)
            );

            -- VOLLEYBALL
            CREATE TABLE IF NOT EXISTS volleyball_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_sets INTEGER DEFAULT 0, team2_sets INTEGER DEFAULT 0,
                current_set INTEGER DEFAULT 1,
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS volleyball_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, set_no INTEGER,
                team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
                winner TEXT, status TEXT DEFAULT 'active',
                FOREIGN KEY(match_id) REFERENCES volleyball_matches(id)
            );
            CREATE TABLE IF NOT EXISTS volleyball_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT, player_no INTEGER DEFAULT 0,
                spikes INTEGER DEFAULT 0, blocks INTEGER DEFAULT 0,
                aces INTEGER DEFAULT 0, digs INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES volleyball_matches(id)
            );

            -- BADMINTON
            CREATE TABLE IF NOT EXISTS badminton_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_games INTEGER DEFAULT 0, team2_games INTEGER DEFAULT 0,
                current_game INTEGER DEFAULT 1,
                match_type TEXT DEFAULT 'best_of_3',
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS badminton_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_no INTEGER,
                team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
                winner TEXT, status TEXT DEFAULT 'active',
                server TEXT, rally_count INTEGER DEFAULT 0,
                t1_streak INTEGER DEFAULT 0, t2_streak INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES badminton_matches(id)
            );
            CREATE TABLE IF NOT EXISTS badminton_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT,
                smashes INTEGER DEFAULT 0, net_kills INTEGER DEFAULT 0, drops INTEGER DEFAULT 0,
                unforced_errors INTEGER DEFAULT 0, service_faults INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES badminton_matches(id)
            );
            CREATE TABLE IF NOT EXISTS badminton_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_id INTEGER,
                team TEXT, shot_type TEXT, fault_type TEXT,
                t1_score_after INTEGER, t2_score_after INTEGER,
                server TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES badminton_matches(id)
            );
            CREATE TABLE IF NOT EXISTS badminton_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_id INTEGER,
                team TEXT, player TEXT, card_type TEXT,
                reason TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES badminton_matches(id)
            );

            -- TABLE TENNIS
            CREATE TABLE IF NOT EXISTS tabletennis_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_games INTEGER DEFAULT 0, team2_games INTEGER DEFAULT 0,
                current_game INTEGER DEFAULT 1,
                match_type TEXT DEFAULT 'best_of_5',
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS tabletennis_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_no INTEGER,
                team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
                winner TEXT, status TEXT DEFAULT 'active',
                server TEXT, rally_count INTEGER DEFAULT 0,
                t1_streak INTEGER DEFAULT 0, t2_streak INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES tabletennis_matches(id)
            );
            CREATE TABLE IF NOT EXISTS tabletennis_players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, team TEXT,
                player_name TEXT,
                smashes INTEGER DEFAULT 0, loops INTEGER DEFAULT 0, drops INTEGER DEFAULT 0,
                unforced_errors INTEGER DEFAULT 0, service_faults INTEGER DEFAULT 0,
                points_won INTEGER DEFAULT 0,
                FOREIGN KEY(match_id) REFERENCES tabletennis_matches(id)
            );
            CREATE TABLE IF NOT EXISTS tabletennis_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_id INTEGER,
                team TEXT, shot_type TEXT, fault_type TEXT,
                t1_score_after INTEGER, t2_score_after INTEGER,
                server TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES tabletennis_matches(id)
            );
            CREATE TABLE IF NOT EXISTS tabletennis_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER, game_id INTEGER,
                team TEXT, player TEXT, card_type TEXT,
                reason TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES tabletennis_matches(id)
            );

            -- CHESS
            CREATE TABLE IF NOT EXISTS chess_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                player1 TEXT, player2 TEXT,
                total_games INTEGER DEFAULT 3,
                player1_score REAL DEFAULT 0,
                player2_score REAL DEFAULT 0,
                current_game INTEGER DEFAULT 1,
                status TEXT DEFAULT 'setup', result TEXT,
                time_control TEXT DEFAULT 'none',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS chess_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                game_no INTEGER,
                white_player TEXT,
                black_player TEXT,
                moves INTEGER DEFAULT 0,
                result TEXT DEFAULT 'pending',
                winner TEXT,
                opening TEXT,
                duration_minutes INTEGER DEFAULT 0,
                notes TEXT,
                pgn TEXT DEFAULT '',
                current_fen TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES chess_matches(id)
            );
            CREATE TABLE IF NOT EXISTS chess_moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER,
                move_no INTEGER,
                san TEXT,
                uci TEXT,
                fen_after TEXT,
                color TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(game_id) REFERENCES chess_games(id)
            );

            -- CARROM
            CREATE TABLE IF NOT EXISTS carrom_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER UNIQUE,
                team1 TEXT, team2 TEXT,
                team1_boards INTEGER DEFAULT 0,
                team2_boards INTEGER DEFAULT 0,
                total_boards INTEGER DEFAULT 3,
                current_board INTEGER DEFAULT 1,
                match_type TEXT DEFAULT 'singles',
                status TEXT DEFAULT 'setup', result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(event_id) REFERENCES events(id)
            );
            CREATE TABLE IF NOT EXISTS carrom_boards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                board_no INTEGER,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                winner TEXT,
                status TEXT DEFAULT 'active',
                FOREIGN KEY(match_id) REFERENCES carrom_matches(id)
            );
            CREATE TABLE IF NOT EXISTS carrom_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                board_id INTEGER,
                team TEXT,
                event_type TEXT,
                points INTEGER DEFAULT 0,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(match_id) REFERENCES carrom_matches(id)
            );
        """)

        if not conn.execute("SELECT id FROM users WHERE role='admin'").fetchone():
            conn.execute(
                "INSERT INTO users(username,email,password_hash,full_name,role) VALUES(?,?,?,?,?)",
                ('admin','admin@gully.com', generate_password_hash('admin123'),'Administrator','admin')
            )
        if conn.execute("SELECT COUNT(*) FROM sports").fetchone()[0] == 0:
            conn.executemany("INSERT INTO sports(name,icon,description) VALUES(?,?,?)", [
                ('Cricket','🏏','The gentlemen\'s game'),
                ('Football','⚽','The beautiful game'),
                ('Basketball','🏀','Fast-paced court sport'),
                ('Volleyball','🏐','Team net sport'),
                ('Badminton','🏸','Racquet sport'),
                ('Kabaddi','🤼','Traditional contact sport'),
                ('Chess','♟️','The royal game of strategy'),
                ('Carrom','🎯','Indoor board sport'),
                ('Table Tennis','🏓','Fast-paced paddle sport'),
            ])
        else:
            # Add Chess, Carrom, and Table Tennis if not already present
            existing = [r[0].lower() for r in conn.execute("SELECT name FROM sports").fetchall()]
            new_sports = []
            if 'chess' not in existing:
                new_sports.append(('Chess','♟️','The royal game of strategy'))
            if 'carrom' not in existing:
                new_sports.append(('Carrom','🎯','Indoor board sport'))
            if 'table tennis' not in existing:
                new_sports.append(('Table Tennis','🏓','Fast-paced paddle sport'))
            if new_sports:
                conn.executemany("INSERT INTO sports(name,icon,description) VALUES(?,?,?)", new_sports)
        conn.commit()


init_db()

# ── DB Migrations (add new columns to existing DBs) ──────────
def migrate_db():
    with get_db() as conn:
        existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(football_matches)").fetchall()]
        for col, defn in [
            ('half_duration', 'INTEGER DEFAULT 45'),
            ('extra_time_1', 'INTEGER DEFAULT 0'),
            ('extra_time_2', 'INTEGER DEFAULT 0'),
            ('timer_started_at', 'REAL DEFAULT 0'),
            ('timer_offset', 'INTEGER DEFAULT 0'),
            ('timer_running', 'INTEGER DEFAULT 0'),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE football_matches ADD COLUMN {col} {defn}")
        # football_players migration
        pl_cols = [r[1] for r in conn.execute("PRAGMA table_info(football_players)").fetchall()]
        for col, defn in [
            ('is_active', 'INTEGER DEFAULT 1'),
            ('is_sub', 'INTEGER DEFAULT 0'),
            ('is_banned', 'INTEGER DEFAULT 0'),
        ]:
            if col not in pl_cols:
                conn.execute(f"ALTER TABLE football_players ADD COLUMN {col} {defn}")
                if col == 'is_active':
                    conn.execute("UPDATE football_players SET is_active=1")
        # football_events migration
        ev_cols = [r[1] for r in conn.execute("PRAGMA table_info(football_events)").fetchall()]
        if 'timer_second' not in ev_cols:
            conn.execute("ALTER TABLE football_events ADD COLUMN timer_second INTEGER DEFAULT 0")
            # Backfill timer_second from minute
            conn.execute("UPDATE football_events SET timer_second = minute * 60")
        # kabaddi_matches migration
        kb_cols = [r[1] for r in conn.execute("PRAGMA table_info(kabaddi_matches)").fetchall()]
        for col, defn in [
            ('t1_empty_raids', 'INTEGER DEFAULT 0'),
            ('t2_empty_raids', 'INTEGER DEFAULT 0'),
            ('timer_half1_sec', 'INTEGER DEFAULT 0'),
            ('timer_half2_sec', 'INTEGER DEFAULT 0'),
            ('timer_running', 'INTEGER DEFAULT 0'),
            ('timer_started_at', 'REAL DEFAULT 0'),
        ]:
            if col not in kb_cols:
                conn.execute(f"ALTER TABLE kabaddi_matches ADD COLUMN {col} {defn}")
        # kabaddi_players: is_bench flag
        kbp_cols = [r[1] for r in conn.execute("PRAGMA table_info(kabaddi_players)").fetchall()]
        if 'is_bench' not in kbp_cols:
            conn.execute("ALTER TABLE kabaddi_players ADD COLUMN is_bench INTEGER DEFAULT 0")
        # Backfill NULL is_bench → 0 for any rows inserted before the column existed
        conn.execute("UPDATE kabaddi_players SET is_bench=0 WHERE is_bench IS NULL")
        if 'is_on_mat' not in kbp_cols:
            conn.execute("ALTER TABLE kabaddi_players ADD COLUMN is_on_mat INTEGER DEFAULT 1")
        if 'raids_attempted' not in kbp_cols:
            conn.execute("ALTER TABLE kabaddi_players ADD COLUMN raids_attempted INTEGER DEFAULT 0")
        if 'raids_successful' not in kbp_cols:
            conn.execute("ALTER TABLE kabaddi_players ADD COLUMN raids_successful INTEGER DEFAULT 0")
        # kabaddi_events: do_or_die flag
        kbe_cols = [r[1] for r in conn.execute("PRAGMA table_info(kabaddi_events)").fetchall()]
        if 'is_do_or_die' not in kbe_cols:
            conn.execute("ALTER TABLE kabaddi_events ADD COLUMN is_do_or_die INTEGER DEFAULT 0")
        # penalty shootout tables (idempotent)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS football_penalty_shootout (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                status TEXT DEFAULT 'active',
                winner TEXT,
                round_no INTEGER DEFAULT 1,
                team1_score INTEGER DEFAULT 0,
                team2_score INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS football_penalty_kicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shootout_id INTEGER,
                round_no INTEGER DEFAULT 1,
                team TEXT,
                player_name TEXT,
                kick_order INTEGER DEFAULT 1,
                result TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # basketball_quarters: team foul tracking per quarter
        bq_cols = [r[1] for r in conn.execute("PRAGMA table_info(basketball_quarters)").fetchall()]
        for col, defn in [('team1_fouls', 'INTEGER DEFAULT 0'), ('team2_fouls', 'INTEGER DEFAULT 0')]:
            if col not in bq_cols:
                conn.execute(f"ALTER TABLE basketball_quarters ADD COLUMN {col} {defn}")
        # basketball_players: technical fouls, foul-out, ejection
        bp_cols = [r[1] for r in conn.execute("PRAGMA table_info(basketball_players)").fetchall()]
        for col, defn in [
            ('technical_fouls', 'INTEGER DEFAULT 0'),
            ('is_fouled_out', 'INTEGER DEFAULT 0'),
            ('is_ejected', 'INTEGER DEFAULT 0'),
        ]:
            if col not in bp_cols:
                conn.execute(f"ALTER TABLE basketball_players ADD COLUMN {col} {defn}")
        conn.commit()

migrate_db()


def migrate_badminton_db():
    """Migrate badminton tables to add new columns."""
    with get_db() as conn:
        bg_cols = [r[1] for r in conn.execute("PRAGMA table_info(badminton_games)").fetchall()]
        for col, defn in [
            ('server', 'TEXT'), ('rally_count', 'INTEGER DEFAULT 0'),
            ('t1_streak', 'INTEGER DEFAULT 0'), ('t2_streak', 'INTEGER DEFAULT 0'),
        ]:
            if col not in bg_cols:
                conn.execute(f"ALTER TABLE badminton_games ADD COLUMN {col} {defn}")
        bp_cols = [r[1] for r in conn.execute("PRAGMA table_info(badminton_players)").fetchall()]
        for col, defn in [
            ('unforced_errors', 'INTEGER DEFAULT 0'), ('service_faults', 'INTEGER DEFAULT 0'),
            ('points_won', 'INTEGER DEFAULT 0'),
        ]:
            if col not in bp_cols:
                conn.execute(f"ALTER TABLE badminton_players ADD COLUMN {col} {defn}")
        conn.execute("""CREATE TABLE IF NOT EXISTS badminton_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_id INTEGER,
            team TEXT, shot_type TEXT, fault_type TEXT,
            t1_score_after INTEGER, t2_score_after INTEGER,
            server TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS badminton_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_id INTEGER,
            team TEXT, player TEXT, card_type TEXT,
            reason TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        # Chess tables
        conn.execute("""CREATE TABLE IF NOT EXISTS chess_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER UNIQUE,
            player1 TEXT, player2 TEXT,
            total_games INTEGER DEFAULT 3,
            player1_score REAL DEFAULT 0,
            player2_score REAL DEFAULT 0,
            current_game INTEGER DEFAULT 1,
            status TEXT DEFAULT 'setup', result TEXT,
            time_control TEXT DEFAULT 'none',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS chess_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_no INTEGER,
            white_player TEXT, black_player TEXT,
            moves INTEGER DEFAULT 0, result TEXT DEFAULT 'pending',
            winner TEXT, opening TEXT,
            duration_minutes INTEGER DEFAULT 0, notes TEXT,
            pgn TEXT DEFAULT '', current_fen TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS chess_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER, move_no INTEGER,
            san TEXT, uci TEXT, fen_after TEXT, color TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        # Carrom tables
        conn.execute("""CREATE TABLE IF NOT EXISTS carrom_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER UNIQUE,
            team1 TEXT, team2 TEXT,
            team1_boards INTEGER DEFAULT 0, team2_boards INTEGER DEFAULT 0,
            total_boards INTEGER DEFAULT 3, current_board INTEGER DEFAULT 1,
            match_type TEXT DEFAULT 'singles',
            status TEXT DEFAULT 'setup', result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS carrom_boards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, board_no INTEGER,
            team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
            winner TEXT, status TEXT DEFAULT 'active')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS carrom_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, board_id INTEGER,
            team TEXT, event_type TEXT, points INTEGER DEFAULT 0, note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.commit()

migrate_badminton_db()


def migrate_tabletennis_db():
    """Create/migrate table tennis tables."""
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS tabletennis_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER UNIQUE,
            team1 TEXT, team2 TEXT,
            team1_games INTEGER DEFAULT 0, team2_games INTEGER DEFAULT 0,
            current_game INTEGER DEFAULT 1,
            match_type TEXT DEFAULT 'best_of_5',
            status TEXT DEFAULT 'setup', result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tabletennis_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_no INTEGER,
            team1_score INTEGER DEFAULT 0, team2_score INTEGER DEFAULT 0,
            winner TEXT, status TEXT DEFAULT 'active',
            server TEXT, rally_count INTEGER DEFAULT 0,
            t1_streak INTEGER DEFAULT 0, t2_streak INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tabletennis_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, team TEXT,
            player_name TEXT,
            smashes INTEGER DEFAULT 0, loops INTEGER DEFAULT 0, drops INTEGER DEFAULT 0,
            unforced_errors INTEGER DEFAULT 0, service_faults INTEGER DEFAULT 0,
            points_won INTEGER DEFAULT 0)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tabletennis_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_id INTEGER,
            team TEXT, shot_type TEXT, fault_type TEXT,
            t1_score_after INTEGER, t2_score_after INTEGER,
            server TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS tabletennis_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER, game_id INTEGER,
            team TEXT, player TEXT, card_type TEXT,
            reason TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        conn.commit()

migrate_tabletennis_db()


def migrate_chess_db():
    """Migrate chess tables to add new columns and chess_moves table."""
    with get_db() as conn:
        cg_cols = [r[1] for r in conn.execute("PRAGMA table_info(chess_games)").fetchall()]
        for col, defn in [('pgn', "TEXT DEFAULT ''"), ('current_fen', "TEXT DEFAULT ''")]:
            if col not in cg_cols:
                conn.execute(f"ALTER TABLE chess_games ADD COLUMN {col} {defn}")
        conn.execute("""CREATE TABLE IF NOT EXISTS chess_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER, move_no INTEGER,
            san TEXT, uci TEXT, fen_after TEXT, color TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
        # Add AI mode columns
        cm_cols = [r[1] for r in conn.execute("PRAGMA table_info(chess_matches)").fetchall()]
        if 'game_mode' not in cm_cols:
            conn.execute("ALTER TABLE chess_matches ADD COLUMN game_mode TEXT DEFAULT 'pvp'")
        if 'ai_difficulty' not in cm_cols:
            conn.execute("ALTER TABLE chess_matches ADD COLUMN ai_difficulty TEXT DEFAULT 'medium'")
        conn.commit()

migrate_chess_db()


def migrate_cricket_ww_db():
    """Add shot_direction to deliveries and ww_direction_enabled to matches."""
    with get_db() as conn:
        cd_cols = [r[1] for r in conn.execute("PRAGMA table_info(cricket_deliveries)").fetchall()]
        if 'shot_direction' not in cd_cols:
            conn.execute("ALTER TABLE cricket_deliveries ADD COLUMN shot_direction REAL DEFAULT NULL")
        cm_cols = [r[1] for r in conn.execute("PRAGMA table_info(cricket_matches)").fetchall()]
        if 'ww_direction_enabled' not in cm_cols:
            conn.execute("ALTER TABLE cricket_matches ADD COLUMN ww_direction_enabled INTEGER DEFAULT 1")
        conn.commit()

migrate_cricket_ww_db()


def migrate_cricket_bowler_tracking_db():
    """Add current_bowler_name to cricket_innings to reliably track who is bowling."""
    with get_db() as conn:
        ci_cols = [r[1] for r in conn.execute("PRAGMA table_info(cricket_innings)").fetchall()]
        if 'current_bowler_name' not in ci_cols:
            conn.execute("ALTER TABLE cricket_innings ADD COLUMN current_bowler_name TEXT DEFAULT NULL")
        conn.commit()

migrate_cricket_bowler_tracking_db()


def migrate_carrom_db():
    """Add player name columns to carrom_matches for existing DBs."""
    with get_db() as conn:
        cm_cols = [r[1] for r in conn.execute("PRAGMA table_info(carrom_matches)").fetchall()]
        if 'team1_players' not in cm_cols:
            conn.execute("ALTER TABLE carrom_matches ADD COLUMN team1_players TEXT DEFAULT ''")
        if 'team2_players' not in cm_cols:
            conn.execute("ALTER TABLE carrom_matches ADD COLUMN team2_players TEXT DEFAULT ''")
        conn.commit()

migrate_carrom_db()


def migrate_player_mode_db():
    """Add player_mode column to badminton and tabletennis matches."""
    with get_db() as conn:
        bd_cols = [r[1] for r in conn.execute("PRAGMA table_info(badminton_matches)").fetchall()]
        if 'player_mode' not in bd_cols:
            conn.execute("ALTER TABLE badminton_matches ADD COLUMN player_mode TEXT DEFAULT 'singles'")
        tt_cols = [r[1] for r in conn.execute("PRAGMA table_info(tabletennis_matches)").fetchall()]
        if 'player_mode' not in tt_cols:
            conn.execute("ALTER TABLE tabletennis_matches ADD COLUMN player_mode TEXT DEFAULT 'singles'")
        conn.commit()

migrate_player_mode_db()


def balls_to_overs(b): return f"{b//6}.{b%6}"
def calc_sr(r,b): return round(r/b*100,2) if b else 0.0
def calc_eco(r,b): return round(r/b*6,2) if b else 0.0

def fmt_dismissal(out_type, bowler, fielder):
    if not out_type: return ''
    b,f = bowler or '',fielder or ''
    return {'bowled':f'b {b}','caught':f'c {f} b {b}' if f else f'c&b {b}',
            'lbw':f'lbw b {b}','run_out':f'run out ({f})' if f else 'run out',
            'stumped':f'st {f} b {b}' if f else f'st b {b}',
            'hit_wicket':f'hit wkt b {b}'}.get(out_type, out_type)

def get_sport_name(ev):
    return (ev.get('sport_name') or '').lower()

def sport_admin_url(event):
    sport = get_sport_name(event)
    eid = event['id']
    if 'cricket' in sport: return url_for('admin_cricket_scoring', eid=eid)
    if 'kabaddi' in sport: return url_for('admin_kabaddi_scoring', eid=eid)
    if 'football' in sport or 'soccer' in sport: return url_for('admin_football_scoring', eid=eid)
    if 'basketball' in sport: return url_for('admin_basketball_scoring', eid=eid)
    if 'volleyball' in sport: return url_for('admin_volleyball_scoring', eid=eid)
    if 'badminton' in sport: return url_for('admin_badminton_scoring', eid=eid)
    if 'table tennis' in sport or 'tabletennis' in sport: return url_for('admin_tabletennis_scoring', eid=eid)
    if 'chess' in sport: return url_for('admin_chess_scoring', eid=eid)
    if 'carrom' in sport: return url_for('admin_carrom_scoring', eid=eid)
    return url_for('admin_event_detail', eid=eid)

app.jinja_env.globals['sport_admin_url'] = sport_admin_url
app.jinja_env.globals['get_sport_name'] = get_sport_name

def current_user():
    """Returns the logged-in user ONLY if they are a regular user (role=user).
    Admin sessions are fully separate and return None here so public pages
    never show admin-specific info or controls."""
    if 'user_id' not in session: return None
    if session.get('role') == 'admin': return None   # admins are NOT users
    with get_db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=? AND role='user'",(session['user_id'],)).fetchone()
        return dict(u) if u else None

def is_admin(): return session.get('role') == 'admin'
def is_user():  return session.get('role') == 'user'

def login_required(f):
    """User-only pages.  Admins are redirected to their own dashboard."""
    @wraps(f)
    def dec(*a,**kw):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return f(*a,**kw)
    return dec

def admin_required(f):
    """Admin-only pages.  Regular users are redirected to admin login."""
    @wraps(f)
    def dec(*a,**kw):
        if not is_admin():
            return redirect(url_for('admin_login'))
        return f(*a,**kw)
    return dec

def get_site_stats():
    with get_db() as conn:
        return {
            'total_events': conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            'live_events': conn.execute("SELECT COUNT(*) FROM events WHERE status='live'").fetchone()[0],
            'total_sports': conn.execute("SELECT COUNT(*) FROM sports WHERE is_active=1").fetchone()[0],
            'total_users': conn.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0],
            'total_regs': conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0],
        }


# ── Sport State Helpers ──────────────────────────────────────

def get_cricket_match_state(match_id):
    with get_db() as conn:
        match = conn.execute("SELECT * FROM cricket_matches WHERE id=?", (match_id,)).fetchone()
        if not match: return None
        match = dict(match)
        innings_list = conn.execute("SELECT * FROM cricket_innings WHERE match_id=? ORDER BY inning_no",(match_id,)).fetchall()
        innings_data = []
        for inn in innings_list:
            inn = dict(inn)
            batters = [dict(b) for b in conn.execute("SELECT * FROM cricket_batting WHERE inning_id=? ORDER BY batting_order",(inn['id'],)).fetchall()]
            bowlers = [dict(b) for b in conn.execute("SELECT * FROM cricket_bowling WHERE inning_id=? ORDER BY id",(inn['id'],)).fetchall()]
            for b in batters:
                b['strike_rate'] = calc_sr(b['runs'],b['balls'])
                b['dismissal'] = fmt_dismissal(b.get('out_type'),b.get('bowler'),b.get('fielder'))
            for b in bowlers:
                b['overs_display'] = balls_to_overs(b['balls'])
                b['economy'] = calc_eco(b['runs'],b['balls'])
            inn['batters'] = batters; inn['bowlers'] = bowlers
            inn['overs_display'] = balls_to_overs(inn['balls'])
            innings_data.append(inn)
        match['innings'] = innings_data
        active = next((i for i in innings_data if i['status']=='active'), None)
        match['current_inning'] = active
        if active and active['inning_no'] == 2:
            first = next((i for i in innings_data if i['inning_no']==1), None)
            if first:
                target = first['total_runs']+1
                runs_needed = target - active['total_runs']
                balls_left = match['total_overs']*6 - active['balls']
                match['target_info'] = {
                    'target':target,'runs_needed':runs_needed,'balls_left':balls_left,
                    'overs_left':balls_to_overs(balls_left),'wickets_left':10-active['wickets'],
                    'rrr':round(runs_needed/balls_left*6,2) if balls_left>0 else 0
                }
        return match


def get_kabaddi_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM kabaddi_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM kabaddi_players WHERE match_id=? AND team=? ORDER BY is_bench ASC, player_no",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM kabaddi_players WHERE match_id=? AND team=? ORDER BY is_bench ASC, player_no",(m['id'],m['team2'])).fetchall()]
        }
        m['events'] = [dict(e) for e in conn.execute("SELECT * FROM kabaddi_events WHERE match_id=? ORDER BY id DESC LIMIT 30",(m['id'],)).fetchall()]
        m['team1_out'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=1 AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team1'])).fetchone()[0]
        m['team2_out'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=1 AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team2'])).fetchone()[0]
        m['team1_on_mat'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=0 AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team1'])).fetchone()[0]
        m['team2_on_mat'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=0 AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team2'])).fetchone()[0]
        m['team1_total'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team1'])).fetchone()[0]
        m['team2_total'] = conn.execute("SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND (is_bench IS NULL OR is_bench!=1)",(m['id'],m['team2'])).fetchone()[0]
        # Do-or-Die: which team has the active do-or-die raid this turn
        t1_empty = m.get('t1_empty_raids', 0) or 0
        t2_empty = m.get('t2_empty_raids', 0) or 0
        m['t1_do_or_die'] = t1_empty >= 2
        m['t2_do_or_die'] = t2_empty >= 2
        # Timer elapsed
        import time as _time
        tr = m.get('timer_running', 0)
        if tr:
            elapsed_extra = int(_time.time() - (m.get('timer_started_at', 0) or 0))
        else:
            elapsed_extra = 0
        if m.get('current_half', 1) == 1:
            m['timer_elapsed'] = (m.get('timer_half1_sec', 0) or 0) + elapsed_extra
        else:
            m['timer_elapsed'] = (m.get('timer_half2_sec', 0) or 0) + elapsed_extra
        m['team1_total'] = len(m['players'][m['team1']])
        m['team2_total'] = len(m['players'][m['team2']])
        # ── Match-level raid statistics ──
        all_evts = [dict(e) for e in conn.execute(
            "SELECT event_type, raiding_team FROM kabaddi_events WHERE match_id=? AND event_type NOT IN ('half_time','substitution','all_out')",
            (m['id'],)).fetchall()]
        for tkey, tname in [('t1', m['team1']), ('t2', m['team2'])]:
            t_raids = [e for e in all_evts if e['raiding_team'] == tname]
            total = len(t_raids)
            success = len([e for e in t_raids if e['event_type'] in ('raid_success', 'super_raid')])
            tackle  = len([e for e in t_raids if e['event_type'] in ('tackle', 'super_tackle')])
            empty   = len([e for e in t_raids if e['event_type'] == 'empty_raid'])
            m[f'{tkey}_raids_total']   = total
            m[f'{tkey}_raids_success'] = success
            m[f'{tkey}_raids_tackle']  = tackle
            m[f'{tkey}_raids_empty']   = empty
            m[f'{tkey}_raid_pct']      = round(success * 100 / total) if total else 0
            m[f'{tkey}_tackle_pct']    = round(tackle * 100 / total) if total else 0
        return m


def get_football_match_state(event_id):
    import time as _time
    with get_db() as conn:
        m = conn.execute("SELECT * FROM football_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? ORDER BY player_no",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? ORDER BY player_no",(m['id'],m['team2'])).fetchall()]
        }
        m['active_players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_active=1 ORDER BY player_no",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_active=1 ORDER BY player_no",(m['id'],m['team2'])).fetchall()]
        }
        m['sub_players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_sub=1 AND is_active=0 AND is_banned=0 ORDER BY player_no",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_sub=1 AND is_active=0 AND is_banned=0 ORDER BY player_no",(m['id'],m['team2'])).fetchall()]
        }
        # banned = red card OR 2+ yellow cards
        m['banned_players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_banned=1 ORDER BY player_no",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND is_banned=1 ORDER BY player_no",(m['id'],m['team2'])).fetchall()]
        }
        m['events'] = [dict(e) for e in conn.execute("SELECT * FROM football_events WHERE match_id=? ORDER BY timer_second ASC,id ASC",(m['id'],)).fetchall()]
        if m.get('timer_running') and m.get('timer_started_at'):
            elapsed = int(_time.time() - m['timer_started_at']) + (m.get('timer_offset') or 0)
        else:
            elapsed = m.get('timer_offset') or 0
        m['timer_elapsed'] = elapsed
        m['timer_minute'] = elapsed // 60
        # Penalty shootout state
        ps = conn.execute("SELECT * FROM football_penalty_shootout WHERE match_id=? ORDER BY id DESC LIMIT 1", (m['id'],)).fetchone()
        if ps:
            ps = dict(ps)
            kicks = [dict(k) for k in conn.execute(
                "SELECT * FROM football_penalty_kicks WHERE shootout_id=? ORDER BY round_no,kick_order", (ps['id'],)).fetchall()]
            ps['kicks'] = kicks
            # Build set of players who already kicked (for new-round UI filtering)
            ps['used_players'] = {
                m['team1']: list({k['player_name'] for k in kicks if k['team']==m['team1']}),
                m['team2']: list({k['player_name'] for k in kicks if k['team']==m['team2']})
            }
            m['penalty_shootout'] = ps
        else:
            m['penalty_shootout'] = None
        return m


def get_basketball_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM basketball_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['quarters'] = [dict(q) for q in conn.execute("SELECT * FROM basketball_quarters WHERE match_id=? ORDER BY quarter_no",(m['id'],)).fetchall()]
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM basketball_players WHERE match_id=? AND team=?",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM basketball_players WHERE match_id=? AND team=?",(m['id'],m['team2'])).fetchall()]
        }
        m['events'] = [dict(e) for e in conn.execute("SELECT * FROM basketball_events WHERE match_id=? ORDER BY id DESC LIMIT 20",(m['id'],)).fetchall()]
        # Current quarter team fouls
        cq = next((q for q in m['quarters'] if q['quarter_no'] == m['current_quarter']), None)
        m['team1_q_fouls'] = cq['team1_fouls'] if cq else 0
        m['team2_q_fouls'] = cq['team2_fouls'] if cq else 0
        # Bonus status: FIBA/NBA per-quarter model (5 fouls = opponent in bonus)
        m['team1_in_bonus'] = m['team2_q_fouls'] >= 5   # t1 benefits when t2 has 5+ fouls
        m['team2_in_bonus'] = m['team1_q_fouls'] >= 5
        m['team1_double_bonus'] = m['team2_q_fouls'] >= 10
        m['team2_double_bonus'] = m['team1_q_fouls'] >= 10
        # Track which subs have been used (came IN via substitution events)
        sub_evs = [dict(e) for e in conn.execute(
            "SELECT * FROM basketball_events WHERE match_id=? AND event_type='substitution' ORDER BY id",(m['id'],)).fetchall()]
        used = {m['team1']:[], m['team2']:[]}
        for ev in sub_evs:
            t = ev.get('team','')
            p = ev.get('player_name','')
            note = ev.get('note','') or ''
            if t in used and p and p not in used[t]:
                used[t].append(p)
        m['used_subs'] = used
        return m


def get_volleyball_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM volleyball_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['sets'] = [dict(s) for s in conn.execute("SELECT * FROM volleyball_sets WHERE match_id=? ORDER BY set_no",(m['id'],)).fetchall()]
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM volleyball_players WHERE match_id=? AND team=?",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM volleyball_players WHERE match_id=? AND team=?",(m['id'],m['team2'])).fetchall()]
        }
        m['current_set'] = next((s for s in m['sets'] if s['status']=='active'), None)
        return m


def get_badminton_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM badminton_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['games'] = [dict(g) for g in conn.execute("SELECT * FROM badminton_games WHERE match_id=? ORDER BY game_no",(m['id'],)).fetchall()]
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM badminton_players WHERE match_id=? AND team=?",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM badminton_players WHERE match_id=? AND team=?",(m['id'],m['team2'])).fetchall()]
        }
        m['current_game'] = next((g for g in m['games'] if g['status']=='active'), None)
        return m


def get_tabletennis_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM tabletennis_matches WHERE event_id=?",(event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['games'] = [dict(g) for g in conn.execute("SELECT * FROM tabletennis_games WHERE match_id=? ORDER BY game_no",(m['id'],)).fetchall()]
        m['players'] = {
            m['team1']:[dict(p) for p in conn.execute("SELECT * FROM tabletennis_players WHERE match_id=? AND team=?",(m['id'],m['team1'])).fetchall()],
            m['team2']:[dict(p) for p in conn.execute("SELECT * FROM tabletennis_players WHERE match_id=? AND team=?",(m['id'],m['team2'])).fetchall()]
        }
        m['current_game'] = next((g for g in m['games'] if g['status']=='active'), None)
        return m


def check_match_started(eid, sport):
    with get_db() as conn:
        if 'cricket' in sport: return bool(conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(eid,)).fetchone())
        if 'kabaddi' in sport: return bool(conn.execute("SELECT id FROM kabaddi_matches WHERE event_id=?",(eid,)).fetchone())
        if 'football' in sport: return bool(conn.execute("SELECT id FROM football_matches WHERE event_id=?",(eid,)).fetchone())
        if 'basketball' in sport: return bool(conn.execute("SELECT id FROM basketball_matches WHERE event_id=?",(eid,)).fetchone())
        if 'volleyball' in sport: return bool(conn.execute("SELECT id FROM volleyball_matches WHERE event_id=?",(eid,)).fetchone())
        if 'badminton' in sport: return bool(conn.execute("SELECT id FROM badminton_matches WHERE event_id=?",(eid,)).fetchone())
        if 'table tennis' in sport or 'tabletennis' in sport: return bool(conn.execute("SELECT id FROM tabletennis_matches WHERE event_id=?",(eid,)).fetchone())
        if 'chess' in sport: return bool(conn.execute("SELECT id FROM chess_matches WHERE event_id=?",(eid,)).fetchone())
        if 'carrom' in sport: return bool(conn.execute("SELECT id FROM carrom_matches WHERE event_id=?",(eid,)).fetchone())
    return False


# ── USER ROUTES ──────────────────────────────────────────────

@app.route('/')
def home():
    with get_db() as conn:
        live = [dict(e) for e in conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.status='live' ORDER BY e.event_date DESC").fetchall()]
        upcoming = [dict(e) for e in conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon,(SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) reg_count FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.status='upcoming' ORDER BY e.event_date ASC LIMIT 12").fetchall()]
        completed = [dict(e) for e in conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.status='completed' ORDER BY e.event_date DESC LIMIT 6").fetchall()]
        sports = [dict(s) for s in conn.execute("SELECT * FROM sports WHERE is_active=1").fetchall()]
        # Inject live scores from sport-specific tables
        def _inject_scores(events):
            for ev in events:
                sn = (ev.get('sport_name') or '').lower()
                eid = ev['id']
                try:
                    if 'cricket' in sn:
                        cm = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?", (eid,)).fetchone()
                        if cm:
                            inn = conn.execute("SELECT inning_no,batting_team,total_runs,total_wickets,total_balls FROM cricket_innings WHERE match_id=? ORDER BY inning_no", (cm['id'],)).fetchall()
                            parts = []
                            for i in inn:
                                balls = i['total_balls'] or 0
                                overs_str = f"{balls//6}.{balls%6}"
                                parts.append(f"{i['batting_team']}: {i['total_runs']}/{i['total_wickets']} ({overs_str}ov)")
                            ev['live_score'] = ' | '.join(parts) if parts else None
                            ev['score_t1'] = None; ev['score_t2'] = None
                    elif 'kabaddi' in sn:
                        m = conn.execute("SELECT team1,team2,team1_score,team2_score FROM kabaddi_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_score']; ev['score_t2'] = m['team2_score']
                    elif 'football' in sn or 'soccer' in sn:
                        m = conn.execute("SELECT team1,team2,team1_score,team2_score FROM football_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_score']; ev['score_t2'] = m['team2_score']
                    elif 'basketball' in sn:
                        m = conn.execute("SELECT team1,team2,team1_score,team2_score FROM basketball_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_score']; ev['score_t2'] = m['team2_score']
                    elif 'volleyball' in sn:
                        m = conn.execute("SELECT team1,team2,team1_sets,team2_sets FROM volleyball_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_sets']; ev['score_t2'] = m['team2_sets']; ev['score_unit'] = 'sets'
                    elif 'badminton' in sn:
                        m = conn.execute("SELECT team1,team2,team1_games,team2_games FROM badminton_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_games']; ev['score_t2'] = m['team2_games']; ev['score_unit'] = 'games'
                    elif 'table tennis' in sn or 'tabletennis' in sn:
                        m = conn.execute("SELECT team1,team2,team1_games,team2_games FROM tabletennis_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_games']; ev['score_t2'] = m['team2_games']; ev['score_unit'] = 'games'
                    elif 'chess' in sn:
                        m = conn.execute("SELECT player1,player2,player1_score,player2_score FROM chess_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['player1_score']; ev['score_t2'] = m['player2_score']; ev['score_unit'] = 'pts'
                    elif 'carrom' in sn:
                        m = conn.execute("SELECT team1,team2,team1_boards,team2_boards FROM carrom_matches WHERE event_id=?", (eid,)).fetchone()
                        if m: ev['score_t1'] = m['team1_boards']; ev['score_t2'] = m['team2_boards']; ev['score_unit'] = 'boards'
                except Exception:
                    pass
            return events
        live = _inject_scores(live)
        completed = _inject_scores(completed)
    return render_template('home.html', live_events=live, upcoming_events=upcoming,
        completed_events=completed, sports=sports, site_stats=get_site_stats(), user=current_user())


@app.route('/login', methods=['GET','POST'])
def login():
    # If already logged in as user, go home
    if 'user_id' in session and session.get('role') == 'user':
        return redirect(url_for('home'))
    # If already logged in as admin, redirect to admin dashboard
    if is_admin():
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        ip = _get_ip()
        locked, wait = _is_rate_limited(ip)
        if locked:
            flash(f'Too many failed attempts. Please wait {wait} seconds before trying again.', 'error')
            return render_template('login.html', user=None)
        uname = request.form.get('username','').strip()
        pwd = request.form.get('password','')
        with get_db() as conn:
            u = conn.execute("SELECT * FROM users WHERE username=? OR email=?",(uname,uname)).fetchone()
        if u and check_password_hash(u['password_hash'],pwd):
            if u['role'] == 'admin':
                # Admin accounts must use the Admin Portal — never the user login
                _record_failed_login(ip)
                flash('This is an admin account. Please use the Admin Portal to sign in.', 'error')
                return render_template('login.html', user=None, show_admin_link=True)
            # Regular user login
            _clear_failed_logins(ip)
            session.clear()
            session.permanent = True
            session.update(user_id=u['id'], username=u['username'], role='user')
            return redirect(request.args.get('next') or url_for('home'))
        _record_failed_login(ip)
        flash('Invalid username or password.','error')
    return render_template('login.html', user=None)


@app.route('/register', methods=['GET','POST'])
def register():
    if 'user_id' in session: return redirect(url_for('home'))
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        email = request.form.get('email','').strip()
        full_name = request.form.get('full_name','').strip()
        phone = request.form.get('phone','').strip()
        password = request.form.get('password','')
        confirm = request.form.get('confirm_password','')
        if not username or not email or not password:
            flash('Username, email and password are required.','error')
            return render_template('register.html', user=None)
        if len(username) < 3:
            flash('Username must be at least 3 characters.','error')
            return render_template('register.html', user=None)
        if len(password) < 6:
            flash('Password must be at least 6 characters.','error')
            return render_template('register.html', user=None)
        if password != confirm:
            flash('Passwords do not match.','error')
            return render_template('register.html', user=None)
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users(username,email,password_hash,full_name,phone,role) VALUES(?,?,?,?,?,?)",
                    (username, email, generate_password_hash(password), full_name, phone, 'user')
                )
                conn.commit()
            flash('Account created! Please sign in.','success')
            return redirect(url_for('login'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash('Username or email already exists.','error')
            else:
                flash(f'Error: {str(e)}','error')
    return render_template('register.html', user=None)


@app.route('/logout')
def logout():
    if session.get('role') == 'admin':
        # Admins should use /admin/logout — redirect them there
        return redirect(url_for('admin_logout'))
    session.clear()
    return redirect(url_for('home'))


@app.route('/event/<int:event_id>')
def view_event(event_id):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(event_id,)).fetchone()
        if not ev: return "Event not found",404
        ev = dict(ev)
        ev['reg_count'] = conn.execute("SELECT COUNT(*) FROM registrations WHERE event_id=?",(event_id,)).fetchone()[0]
        user_reg = None
        # Only look up registration for regular users — admins have no event registrations
        if 'user_id' in session and session.get('role') == 'user':
            row = conn.execute("SELECT * FROM registrations WHERE user_id=? AND event_id=?",(session['user_id'],event_id)).fetchone()
            user_reg = dict(row) if row else None

    sport = get_sport_name(ev)
    cricket_state = kabaddi_state = football_state = basketball_state = volleyball_state = badminton_state = None
    chess_state = carrom_state = tabletennis_state = None

    if 'cricket' in sport:
        with get_db() as conn:
            cm = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(event_id,)).fetchone()
        if cm: cricket_state = get_cricket_match_state(cm['id'])
    elif 'kabaddi' in sport:
        kabaddi_state = get_kabaddi_match_state(event_id)
    elif 'football' in sport:
        football_state = get_football_match_state(event_id)
    elif 'basketball' in sport:
        basketball_state = get_basketball_match_state(event_id)
    elif 'volleyball' in sport:
        volleyball_state = get_volleyball_match_state(event_id)
    elif 'badminton' in sport:
        badminton_state = get_badminton_match_state(event_id)
    elif 'chess' in sport:
        chess_state = get_chess_match_state(event_id)
    elif 'carrom' in sport:
        carrom_state = get_carrom_match_state(event_id)
    elif 'table tennis' in sport or 'tabletennis' in sport:
        tabletennis_state = get_tabletennis_match_state(event_id)

    return render_template('event.html', event=ev,
        cricket_state=cricket_state, kabaddi_state=kabaddi_state,
        football_state=football_state, basketball_state=basketball_state,
        volleyball_state=volleyball_state, badminton_state=badminton_state,
        chess_state=chess_state, carrom_state=carrom_state,
        tabletennis_state=tabletennis_state,
        user_reg=user_reg, user=current_user())


@app.route('/event/<int:event_id>/register', methods=['POST'])
@login_required
def register_event(event_id):
    with get_db() as conn:
        ev = conn.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone()
        if not ev or ev['status'] not in ('upcoming','live'):
            flash('Registration closed','error')
            return redirect(url_for('view_event',event_id=event_id))
        if ev['max_registrations']>0:
            cnt = conn.execute("SELECT COUNT(*) FROM registrations WHERE event_id=?",(event_id,)).fetchone()[0]
            if cnt>=ev['max_registrations']:
                flash('Registration full!','error')
                return redirect(url_for('view_event',event_id=event_id))
        try:
            conn.execute("INSERT INTO registrations(user_id,event_id,team_preference,position,notes) VALUES(?,?,?,?,?)",
                (session['user_id'],event_id,request.form.get('team_preference',''),request.form.get('position',''),request.form.get('notes','')))
            conn.commit()
            flash('Registered successfully! 🎉','success')
        except sqlite3.IntegrityError:
            flash('Already registered','info')
    return redirect(url_for('view_event',event_id=event_id))


@app.route('/profile')
@login_required
def profile():
    # login_required already blocks admins; this is a pure user page
    with get_db() as conn:
        u = dict(conn.execute("SELECT * FROM users WHERE id=?",(session['user_id'],)).fetchone())
        regs = [dict(r) for r in conn.execute("SELECT r.*,e.title,e.team1,e.team2,e.event_date,e.venue,e.status event_status,s.name sport_name,s.icon sport_icon FROM registrations r JOIN events e ON r.event_id=e.id LEFT JOIN sports s ON e.sport_id=s.id WHERE r.user_id=? ORDER BY r.registered_at DESC",(session['user_id'],)).fetchall()]
    return render_template('profile.html',user=u,registrations=regs)


@app.route('/matches')
def matches_page():
    fstatus = request.args.get('status','all')
    fsport  = request.args.get('sport','all')
    with get_db() as conn:
        q = "SELECT e.*,s.name sport_name,s.icon sport_icon,(SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) reg_count FROM events e LEFT JOIN sports s ON e.sport_id=s.id"
        conds,params = [],[]
        if fstatus!='all': conds.append("e.status=?"); params.append(fstatus)
        if fsport!='all': conds.append("e.sport_id=?"); params.append(fsport)
        if conds: q+=" WHERE "+" AND ".join(conds)
        q+=" ORDER BY CASE e.status WHEN 'live' THEN 0 WHEN 'upcoming' THEN 1 ELSE 2 END, e.event_date DESC"
        events = [dict(e) for e in conn.execute(q,params).fetchall()]
        sports = [dict(s) for s in conn.execute("SELECT * FROM sports WHERE is_active=1").fetchall()]
        total_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        live_count = conn.execute("SELECT COUNT(*) FROM events WHERE status='live'").fetchone()[0]
    return render_template('matches.html',events=events,sports=sports,
        filter_status=fstatus,filter_sport=fsport,total_events=total_events,live_count=live_count,user=current_user())


@app.route('/sports-directory')
def sports_directory():
    with get_db() as conn:
        sports = [dict(s) for s in conn.execute("SELECT s.*,COUNT(DISTINCT e.id) total_events,COUNT(DISTINCT CASE WHEN e.status='live' THEN e.id END) live_events,COUNT(DISTINCT CASE WHEN e.status='upcoming' THEN e.id END) upcoming_events,COUNT(DISTINCT CASE WHEN e.status='completed' THEN e.id END) completed_events,COUNT(DISTINCT r.id) total_registrations FROM sports s LEFT JOIN events e ON e.sport_id=s.id LEFT JOIN registrations r ON r.event_id=e.id GROUP BY s.id ORDER BY s.name").fetchall()]
    return render_template('sports_directory.html',sports=sports,total_sports=len(sports),total_active=sum(1 for s in sports if s['is_active']),user=current_user())


@app.route('/leaderboard')
def leaderboard():
    with get_db() as conn:
        top_scorers = [dict(r) for r in conn.execute("SELECT b.player_name,SUM(b.runs) total_runs,SUM(b.balls) total_balls,SUM(b.fours) total_fours,SUM(b.sixes) total_sixes,MAX(b.runs) best_score,COUNT(DISTINCT b.inning_id) innings FROM cricket_batting b WHERE b.balls>0 GROUP BY b.player_name ORDER BY total_runs DESC LIMIT 20").fetchall()]
        top_bowlers = [dict(r) for r in conn.execute("SELECT b.player_name,SUM(b.wickets) total_wickets,SUM(b.balls) total_balls,SUM(b.runs) total_runs,SUM(b.maidens) total_maidens,COUNT(DISTINCT b.inning_id) innings FROM cricket_bowling b WHERE b.balls>0 GROUP BY b.player_name ORDER BY total_wickets DESC LIMIT 20").fetchall()]
        kabaddi_leaders = [dict(r) for r in conn.execute("SELECT player_name,team,SUM(raid_points) raid_pts,SUM(tackle_points) tackle_pts,SUM(super_tackles) super_tackles,SUM(bonus_points) bonus_pts FROM kabaddi_players GROUP BY player_name ORDER BY (SUM(raid_points)+SUM(tackle_points)) DESC LIMIT 20").fetchall()]
        sports = [dict(s) for s in conn.execute("SELECT * FROM sports WHERE is_active=1").fetchall()]
    for p in top_scorers: p['strike_rate'] = round(p['total_runs']/p['total_balls']*100,1) if p['total_balls'] else 0
    for p in top_bowlers:
        p['economy'] = round(p['total_runs']/p['total_balls']*6,2) if p['total_balls'] else 0
        p['overs'] = f"{p['total_balls']//6}.{p['total_balls']%6}"
    return render_template('leaderboard.html',top_scorers=top_scorers,top_bowlers=top_bowlers,kabaddi_leaders=kabaddi_leaders,sports=sports,user=current_user())


@app.route('/scoreboard')
def scoreboard():
    match_id = request.args.get('match_id',None)
    if match_id:
        try: match_id=int(match_id)
        except: match_id=None
    return render_template('scoreboard.html',match_id=match_id)


@app.route('/scoreboard/event/<int:eid>')
def scoreboard_event(eid):
    with get_db() as conn:
        cm = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(eid,)).fetchone()
    return render_template('scoreboard.html',match_id=cm['id'] if cm else None)


# ── ADMIN ROUTES ──────────────────────────────────────────────

@app.route('/admin')
@app.route('/admin/')
def admin_redirect(): return redirect(url_for('admin_dashboard') if is_admin() else url_for('admin_login'))


@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    # Already logged in as admin
    if is_admin(): return redirect(url_for('admin_dashboard'))
    # If a regular user is logged in, they cannot access admin — show warning
    if 'user_id' in session and session.get('role') == 'user':
        session.clear()   # wipe user session before admin login
    if request.method=='POST':
        ip = _get_ip()
        locked, wait = _is_rate_limited(ip)
        if locked:
            flash(f'Too many failed attempts. Please wait {wait} seconds.', 'error')
            return render_template('admin/login.html')
        uname = request.form.get('username','').strip()
        pwd = request.form.get('password','')
        with get_db() as conn:
            u = conn.execute("SELECT * FROM users WHERE (username=? OR email=?) AND role='admin'",(uname,uname)).fetchone()
        if u and check_password_hash(u['password_hash'],pwd):
            _clear_failed_logins(ip)
            session.clear()   # ensure clean slate
            session.permanent = True
            session['user_id']  = u['id']
            session['username'] = u['username']
            session['role']     = 'admin'
            return redirect(url_for('admin_dashboard'))
        _record_failed_login(ip)
        flash('Invalid admin credentials. Admin accounts only.','error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    with get_db() as conn:
        stats = {
            'users': conn.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()[0],
            'events': conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            'live': conn.execute("SELECT COUNT(*) FROM events WHERE status='live'").fetchone()[0],
            'upcoming': conn.execute("SELECT COUNT(*) FROM events WHERE status='upcoming'").fetchone()[0],
            'sports': conn.execute("SELECT COUNT(*) FROM sports").fetchone()[0],
            'registrations': conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0],
        }
        recent_events = [dict(e) for e in conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon,(SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) reg_count FROM events e LEFT JOIN sports s ON e.sport_id=s.id ORDER BY e.created_at DESC LIMIT 8").fetchall()]
        recent_users = [dict(u) for u in conn.execute("SELECT * FROM users WHERE role='user' ORDER BY created_at DESC LIMIT 6").fetchall()]
        # Sport-wise event counts
        sport_stats = [dict(r) for r in conn.execute("SELECT s.name,s.icon,COUNT(e.id) total,SUM(CASE WHEN e.status='live' THEN 1 ELSE 0 END) live_cnt,SUM(CASE WHEN e.status='completed' THEN 1 ELSE 0 END) done FROM sports s LEFT JOIN events e ON e.sport_id=s.id GROUP BY s.id ORDER BY total DESC").fetchall()]
    return render_template('admin/dashboard.html',stats=stats,recent_events=recent_events,recent_users=recent_users,sport_stats=sport_stats,admin_name=session.get('username'))


@app.route('/admin/sports')
@admin_required
def admin_sports():
    with get_db() as conn:
        sports = [dict(s) for s in conn.execute("SELECT s.*,COUNT(e.id) event_count FROM sports s LEFT JOIN events e ON e.sport_id=s.id GROUP BY s.id ORDER BY s.name").fetchall()]
    return render_template('admin/sports.html',sports=sports)


@app.route('/admin/sports/add', methods=['POST'])
@admin_required
def admin_add_sport():
    name = request.form.get('name','').strip()
    if name:
        with get_db() as conn:
            conn.execute("INSERT INTO sports(name,icon,description) VALUES(?,?,?)",(name,request.form.get('icon','🏆'),request.form.get('description','')))
            conn.commit()
        flash(f'Sport "{name}" added!','success')
    return redirect(url_for('admin_sports'))


@app.route('/admin/sports/<int:sid>/toggle', methods=['POST'])
@admin_required
def admin_toggle_sport(sid):
    with get_db() as conn:
        conn.execute("UPDATE sports SET is_active=1-is_active WHERE id=?",(sid,)); conn.commit()
    return redirect(url_for('admin_sports'))


@app.route('/admin/sports/<int:sid>/delete', methods=['POST'])
@admin_required
def admin_delete_sport(sid):
    with get_db() as conn:
        conn.execute("DELETE FROM sports WHERE id=?",(sid,)); conn.commit()
    flash('Sport deleted','success')
    return redirect(url_for('admin_sports'))


@app.route('/admin/events')
@admin_required
def admin_events():
    fs = request.args.get('status','all')
    fsp = request.args.get('sport','all')
    with get_db() as conn:
        q = "SELECT e.*,s.name sport_name,s.icon sport_icon,(SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) reg_count FROM events e LEFT JOIN sports s ON e.sport_id=s.id"
        conds,params = [],[]
        if fs!='all': conds.append("e.status=?"); params.append(fs)
        if fsp!='all': conds.append("e.sport_id=?"); params.append(fsp)
        if conds: q+=" WHERE "+" AND ".join(conds)
        q+=" ORDER BY e.event_date DESC"
        events = [dict(e) for e in conn.execute(q,params).fetchall()]
        sports = [dict(s) for s in conn.execute("SELECT * FROM sports WHERE is_active=1").fetchall()]
    return render_template('admin/events.html',events=events,sports=sports,filter_status=fs,filter_sport=fsp)


@app.route('/admin/events/add', methods=['POST'])
@admin_required
def admin_add_event():
    sport_id = request.form.get('sport_id') or None
    with get_db() as conn:
        # Resolve sport name for config lookup
        sport_name = ''
        if sport_id:
            sr = conn.execute("SELECT name FROM sports WHERE id=?", (sport_id,)).fetchone()
            if sr: sport_name = sr['name'].lower()

        cur = conn.execute(
            "INSERT INTO events(sport_id,title,team1,team2,venue,event_date,event_time,description,max_registrations,total_overs,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sport_id, request.form.get('title','').strip(),
             request.form.get('team1','').strip(), request.form.get('team2','').strip(),
             request.form.get('venue','').strip(), request.form.get('event_date',''),
             request.form.get('event_time',''), request.form.get('description','').strip(),
             int(request.form.get('max_registrations',0) or 0),
             int(request.form.get('total_overs',6) or 6),
             request.form.get('status','upcoming')))
        eid = cur.lastrowid

        # Player counts by sport
        SPORT_COUNTS = {
            'cricket':    {'main':11, 'subs':5},
            'kabaddi':    {'main':7,  'subs':5},
            'football':   {'main':11, 'subs':5},
            'soccer':     {'main':11, 'subs':5},
            'basketball': {'main':7,  'subs':5},
            'volleyball': {'main':6,  'subs':3},
            'badminton':  {'main':2,  'subs':0},
            'chess':      {'main':1,  'subs':0},
            'carrom':     {'main':2,  'subs':0},
            'table tennis': {'main':1, 'subs':0},
            'tabletennis':  {'main':1, 'subs':0},
        }
        counts = None
        for key, val in SPORT_COUNTS.items():
            if key in sport_name:
                counts = val
                break

        if counts:
            impact_enabled = True  # always allow subs for all sports
            effective_subs = counts['subs']

            for team_key in ['team1', 'team2']:
                for i in range(1, counts['main'] + 1):
                    pname = request.form.get(f'{team_key}_p{i}_name', '').strip()
                    prole = request.form.get(f'{team_key}_p{i}_role', 'player')
                    if pname:
                        conn.execute(
                            "INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,?,?)",
                            (eid, team_key, pname, prole, 0, i))
                        # Also save to cricket_event_players for cricket backward compat
                        if 'cricket' in sport_name:
                            conn.execute(
                                "INSERT INTO cricket_event_players(event_id,team,player_name,role,is_impact,player_order) VALUES(?,?,?,?,?,?)",
                                (eid, team_key, pname, prole, 0, i))
                for i in range(1, effective_subs + 1):
                    sname = request.form.get(f'{team_key}_sub{i}_name', '').strip()
                    srole = request.form.get(f'{team_key}_sub{i}_role', 'player')
                    if sname:
                        conn.execute(
                            "INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,?,?)",
                            (eid, team_key, sname, srole, 1, i))
                        if 'cricket' in sport_name:
                            conn.execute(
                                "INSERT INTO cricket_event_players(event_id,team,player_name,role,is_impact,player_order) VALUES(?,?,?,?,?,?)",
                                (eid, team_key, sname, srole, 1, i))
        conn.commit()
    flash('Event created!', 'success')
    return redirect(url_for('admin_events'))


@app.route('/admin/events/<int:eid>', methods=['GET','POST'])
@admin_required
def admin_event_detail(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        if request.method=='POST':
            conn.execute("UPDATE events SET sport_id=?,title=?,team1=?,team2=?,venue=?,event_date=?,event_time=?,description=?,max_registrations=?,total_overs=?,status=? WHERE id=?",
                (request.form.get('sport_id') or None,request.form.get('title','').strip(),
                 request.form.get('team1','').strip(),request.form.get('team2','').strip(),
                 request.form.get('venue','').strip(),request.form.get('event_date',''),
                 request.form.get('event_time',''),request.form.get('description','').strip(),
                 int(request.form.get('max_registrations',0) or 0),int(request.form.get('total_overs',6) or 6),
                 request.form.get('status','upcoming'),eid))
            conn.commit()
            flash('Event updated!','success')
            return redirect(url_for('admin_event_detail',eid=eid))
        ev = dict(ev)
        regs = [dict(r) for r in conn.execute("SELECT r.*,u.username,u.full_name,u.email,u.phone FROM registrations r JOIN users u ON r.user_id=u.id WHERE r.event_id=? ORDER BY r.registered_at DESC",(eid,)).fetchall()]
        sports = [dict(s) for s in conn.execute("SELECT * FROM sports WHERE is_active=1").fetchall()]
    sport = get_sport_name(ev)
    match_started = check_match_started(eid, sport)
    return render_template('admin/event_detail.html',event=ev,registrations=regs,sports=sports,match_started=match_started)


@app.route('/admin/events/<int:eid>/delete', methods=['POST'])
@admin_required
def admin_delete_event(eid):
    with get_db() as conn:
        conn.execute("DELETE FROM events WHERE id=?",(eid,)); conn.commit()
    flash('Event deleted','success')
    return redirect(url_for('admin_events'))


@app.route('/admin/users')
@admin_required
def admin_users():
    with get_db() as conn:
        users = [dict(u) for u in conn.execute("SELECT u.*,(SELECT COUNT(*) FROM registrations r WHERE r.user_id=u.id) reg_count FROM users u WHERE u.role='user' ORDER BY u.created_at DESC").fetchall()]
    return render_template('admin/users.html',users=users)



@app.route('/admin/registrations')
@admin_required
def admin_registrations():
    with get_db() as conn:
        regs = [dict(r) for r in conn.execute("SELECT r.*,u.username,u.full_name,u.email,e.title event_title,e.team1,e.team2,e.event_date,s.name sport_name,s.icon sport_icon FROM registrations r JOIN users u ON r.user_id=u.id JOIN events e ON r.event_id=e.id LEFT JOIN sports s ON e.sport_id=s.id ORDER BY r.registered_at DESC").fetchall()]
    return render_template('admin/registrations.html',registrations=regs)


@app.route('/admin/registrations/<int:rid>/status', methods=['POST'])
@admin_required
def admin_reg_status(rid):
    with get_db() as conn:
        conn.execute("UPDATE registrations SET status=? WHERE id=?",(request.form.get('status','pending'),rid)); conn.commit()
    return redirect(request.referrer or url_for('admin_registrations'))


# ── CRICKET ADMIN ──────────────────────────────────────────

@app.route('/admin/cricket/<int:eid>')
@admin_required
def admin_cricket_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
    if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
    with get_db() as conn:
        cm = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(eid,)).fetchone()
        # Load pre-configured players from event setup
        setup_players = [dict(r) for r in conn.execute(
            "SELECT * FROM cricket_event_players WHERE event_id=? ORDER BY team,is_impact,player_order",(eid,)).fetchall()]
    cricket_match_id = cm['id'] if cm else None
    ev_dict = dict(ev)
    ev_dict['cricket_match_id'] = cricket_match_id
    # Prepare player lists by team for pre-filling
    setup = {'team1': {'main': [], 'subs': []}, 'team2': {'main': [], 'subs': []}}
    for p in setup_players:
        team_key = p['team']  # 'team1' or 'team2'
        if team_key in setup:
            if p['is_impact']:
                setup[team_key]['subs'].append(p)
            else:
                setup[team_key]['main'].append(p)
    ev_dict['player_setup'] = setup
    return render_template('admin/cricket_scoring.html',event=ev_dict)




@app.route('/admin/events/<int:eid>/start-cricket', methods=['POST'])
@admin_required
def admin_start_cricket(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(eid,)).fetchone():
            conn.execute("INSERT INTO cricket_matches(event_id,team1,team2,total_overs,status) VALUES(?,?,?,?,?)",
                (eid,ev['team1'],ev['team2'],ev.get('total_overs',6),'setup'))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,))
            conn.commit()
    return redirect(url_for('admin_cricket_scoring',eid=eid))


# ── KABADDI ADMIN ──────────────────────────────────────────

@app.route('/admin/kabaddi/<int:eid>')
@admin_required
def admin_kabaddi_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order",(eid,)).fetchall()
    state = get_kabaddi_match_state(eid)
    t1_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team1' and not p['is_sub'])
    t2_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team2' and not p['is_sub'])
    t1_subs = [p['player_name'] for p in ep if p['team']=='team1' and p['is_sub']]
    t2_subs = [p['player_name'] for p in ep if p['team']=='team2' and p['is_sub']]
    return render_template('admin/kabaddi_scoring.html',event=dict(ev),state=state,
        preset_t1=t1_players, preset_t2=t2_players,
        t1_subs=t1_subs, t2_subs=t2_subs)


@app.route('/admin/kabaddi/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_kabaddi(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM kabaddi_matches WHERE event_id=?",(eid,)).fetchone():
            cur = conn.execute("INSERT INTO kabaddi_matches(event_id,team1,team2,status) VALUES(?,?,?,?)",
                (eid,ev['team1'],ev['team2'],'live'))
            mid = cur.lastrowid
            t1p = [p.strip() for p in request.form.get('team1_players','').split('\n') if p.strip()]
            t2p = [p.strip() for p in request.form.get('team2_players','').split('\n') if p.strip()]
            if not t1p:
                t1p = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team='team1' AND is_sub=0 ORDER BY player_order",(eid,)).fetchall()]
            if not t2p:
                t2p = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team='team2' AND is_sub=0 ORDER BY player_order",(eid,)).fetchall()]
            for i,p in enumerate(t1p):
                conn.execute("INSERT INTO kabaddi_players(match_id,team,player_name,player_no) VALUES(?,?,?,?)",(mid,ev['team1'],p,i+1))
            for i,p in enumerate(t2p):
                conn.execute("INSERT INTO kabaddi_players(match_id,team,player_name,player_no) VALUES(?,?,?,?)",(mid,ev['team2'],p,i+1))
            # Save subs and main players to event_players
            for tk,plist in [('team1',t1p),('team2',t2p)]:
                subs_raw = [x.strip() for x in request.form.get(f'{tk}_subs','').split('\n') if x.strip()]
                if subs_raw:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=1",(eid,tk))
                    for i,s in enumerate(subs_raw):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,1,?)",(eid,tk,s,'player',i+1))
                if plist:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=0",(eid,tk))
                    for i,p in enumerate(plist):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)",(eid,tk,p,'player',i+1))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,))
            conn.commit()
        else:
            flash('Match already started','info')
    return redirect(url_for('admin_kabaddi_scoring',eid=eid))


@app.route('/api/kabaddi/<int:eid>/state')
def api_kabaddi_state(eid):
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'not found'}),404
    return jsonify(state)


@app.route('/api/kabaddi/<int:eid>/raid', methods=['POST'])
@admin_required
def api_kabaddi_raid(eid):
    import time as _time
    d = request.json
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']

    raiding_team = d.get('raiding_team')
    defending_team = state['team2'] if raiding_team == state['team1'] else state['team1']
    raider_name = d.get('raider_name','')
    touched_players = d.get('touched_players',[])
    got_caught = d.get('got_caught', False)
    got_bonus = d.get('got_bonus', False)
    is_empty = d.get('is_empty', False)
    is_do_or_die = d.get('is_do_or_die', False)

    # Count active defenders on mat
    with get_db() as conn:
        defenders_on_mat = conn.execute(
            "SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=0 AND (is_bench IS NULL OR is_bench!=1)",
            (mid, defending_team)).fetchone()[0]
    
    num_touched = len(touched_players)
    is_super_raid = num_touched >= 3
    
    # Super Tackle: defending team has ≤3 players on mat AND they tackle
    is_super_tackle = got_caught and defenders_on_mat <= 3
    
    # Bonus point: only valid when ≥6 defenders on mat
    valid_bonus = got_bonus and defenders_on_mat >= 6 and not got_caught

    raiding_pts = 0
    defending_pts = 0

    # Do-or-Die empty raid → raider is OUT, defending team +1 (treat like a tackle)
    dod_failed = is_do_or_die and is_empty
    if dod_failed:
        defending_pts = 1
        got_caught = True   # raider goes OUT via normal OUT path below
        is_empty = False    # don't treat as empty for scoring
    elif is_empty or (not got_caught and num_touched == 0 and not valid_bonus):
        # Empty raid
        raiding_pts = 0
    elif not got_caught:
        raiding_pts = num_touched + (1 if valid_bonus else 0)
    else:
        defending_pts = 2 if is_super_tackle else 1

    with get_db() as conn:
        # Determine event type
        if dod_failed: evt_type = 'dod_failed'
        elif is_super_tackle: evt_type = 'super_tackle'
        elif got_caught: evt_type = 'tackle'
        elif is_super_raid: evt_type = 'super_raid'
        elif raiding_pts > 0: evt_type = 'raid_success'
        else: evt_type = 'empty_raid'

        cur = conn.execute(
            "INSERT INTO kabaddi_events(match_id,half_no,event_type,raiding_team,defending_team,raider_name,points_raiding,points_defending,is_super_tackle,is_bonus,is_super_raid,is_do_or_die) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid,state['current_half'],evt_type,raiding_team,defending_team,raider_name,raiding_pts,defending_pts,
             1 if is_super_tackle else 0, 1 if valid_bonus else 0, 1 if is_super_raid else 0, 1 if is_do_or_die else 0)
        )
        evid = cur.lastrowid

        # Mark touched defenders as OUT
        for pname in touched_players:
            conn.execute("UPDATE kabaddi_players SET is_out=1 WHERE match_id=? AND team=? AND player_name=? AND (is_bench IS NULL OR is_bench!=1)",(mid,defending_team,pname))
            conn.execute("INSERT INTO kabaddi_out_players(event_id,player_name,team) VALUES(?,?,?)",(evid,pname,defending_team))

        # Mark raider as OUT if caught (or do-or-die failed)
        if got_caught and raider_name:
            conn.execute("UPDATE kabaddi_players SET is_out=1 WHERE match_id=? AND team=? AND player_name=? AND (is_bench IS NULL OR is_bench!=1)",(mid,raiding_team,raider_name))

        # Update player stats — raid counts
        # raid_points stores touch points only; bonus_points tracks bonus separately
        touch_pts = num_touched  # exclude bonus so totals don't double-count
        if raider_name and evt_type != 'empty_raid':
            is_raid_success = raiding_pts > 0
            conn.execute("UPDATE kabaddi_players SET raids_attempted=raids_attempted+1, raids_successful=raids_successful+? WHERE match_id=? AND team=? AND player_name=?",
                (1 if is_raid_success else 0, mid, raiding_team, raider_name))
        if raider_name:
            conn.execute("UPDATE kabaddi_players SET raid_points=raid_points+?,bonus_points=bonus_points+? WHERE match_id=? AND team=? AND player_name=?",
                (touch_pts, 1 if valid_bonus else 0, mid, raiding_team, raider_name))
        if got_caught:
            tackle_pts = 2 if is_super_tackle else 1
            for tname in d.get('tacklers',[]):
                conn.execute("UPDATE kabaddi_players SET tackle_points=tackle_points+?,super_tackles=super_tackles+? WHERE match_id=? AND team=? AND player_name=?",
                    (tackle_pts, 1 if is_super_tackle else 0, mid, defending_team, tname))

        # Update match score
        if raiding_team == state['team1']:
            conn.execute("UPDATE kabaddi_matches SET team1_score=team1_score+?,team2_score=team2_score+? WHERE id=?",(raiding_pts,defending_pts,mid))
        else:
            conn.execute("UPDATE kabaddi_matches SET team2_score=team2_score+?,team1_score=team1_score+? WHERE id=?",(raiding_pts,defending_pts,mid))

        # ── REVIVAL RULE (Pro Kabaddi): Revive N out-players equal to TOUCH points scored (FIFO) ──
        # Bonus points give +1 to team score but do NOT revive players (Pro Kabaddi rule)
        raiding_revived = 0
        defending_revived = 0
        touch_pts_for_revival = num_touched  # only touch points trigger revivals, not bonus
        if touch_pts_for_revival > 0:
            out_raiders = conn.execute(
                "SELECT id FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=1 AND (is_bench IS NULL OR is_bench!=1) ORDER BY id",
                (mid, raiding_team)).fetchall()
            revive_count = min(touch_pts_for_revival, len(out_raiders))
            for row in out_raiders[:revive_count]:
                conn.execute("UPDATE kabaddi_players SET is_out=0, revivals=revivals+1 WHERE id=?", (row['id'],))
            raiding_revived = revive_count

        if defending_pts > 0:
            out_defenders = conn.execute(
                "SELECT id FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=1 AND (is_bench IS NULL OR is_bench!=1) ORDER BY id",
                (mid, defending_team)).fetchall()
            revive_count = min(defending_pts, len(out_defenders))
            for row in out_defenders[:revive_count]:
                conn.execute("UPDATE kabaddi_players SET is_out=0, revivals=revivals+1 WHERE id=?", (row['id'],))
            defending_revived = revive_count

        # ── DO-OR-DIE tracking: track consecutive empty raids ──
        t1_empty = state.get('t1_empty_raids', 0) or 0
        t2_empty = state.get('t2_empty_raids', 0) or 0
        if evt_type == 'empty_raid':
            if raiding_team == state['team1']:
                t1_empty += 1
            else:
                t2_empty += 1
        else:
            # Any non-empty raid resets the counter for that team
            if raiding_team == state['team1']:
                t1_empty = 0
            else:
                t2_empty = 0
        # Do-or-Die fired: also reset after do-or-die (whether success or fail)
        if is_do_or_die:
            if raiding_team == state['team1']:
                t1_empty = 0
            else:
                t2_empty = 0
        conn.execute("UPDATE kabaddi_matches SET t1_empty_raids=?,t2_empty_raids=? WHERE id=?",(t1_empty,t2_empty,mid))

        # ── CHECK ALL-OUT ──
        defending_out = conn.execute(
            "SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND is_out=1 AND (is_bench IS NULL OR is_bench!=1)",(mid,defending_team)).fetchone()[0]
        total_on_mat = conn.execute(
            "SELECT COUNT(*) FROM kabaddi_players WHERE match_id=? AND team=? AND (is_bench IS NULL OR is_bench!=1)",(mid,defending_team)).fetchone()[0]
        all_out = False

        if total_on_mat > 0 and defending_out >= total_on_mat:
            all_out = True
            conn.execute("INSERT INTO kabaddi_events(match_id,half_no,event_type,raiding_team,defending_team,points_raiding,is_all_out,note) VALUES(?,?,?,?,?,?,?,?)",
                (mid,state['current_half'],'all_out',raiding_team,defending_team,2,1,f'ALL OUT! {defending_team} — {raiding_team} gets +2 bonus'))
            # Revive ALL defending team players
            conn.execute("UPDATE kabaddi_players SET is_out=0,revivals=revivals+1 WHERE match_id=? AND team=? AND (is_bench IS NULL OR is_bench!=1)",(mid,defending_team))
            if raiding_team==state['team1']:
                conn.execute("UPDATE kabaddi_matches SET team1_score=team1_score+2 WHERE id=?",(mid,))
            else:
                conn.execute("UPDATE kabaddi_matches SET team2_score=team2_score+2 WHERE id=?",(mid,))

        conn.commit()

    updated = get_kabaddi_match_state(eid)
    return jsonify({'success':True,'state':updated,'all_out':all_out,'all_out_team':defending_team if all_out else None,
                    'is_super_tackle': is_super_tackle, 'is_super_raid': is_super_raid, 'dod_failed': dod_failed,
                    'defenders_on_mat': defenders_on_mat,
                    'points_raiding': raiding_pts, 'points_defending': defending_pts,
                    'raiding_revived': raiding_revived, 'defending_revived': defending_revived})


@app.route('/api/kabaddi/<int:eid>/switch-half', methods=['POST'])
@admin_required
def api_kabaddi_switch_half(eid):
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']
    with get_db() as conn:
        conn.execute("UPDATE kabaddi_matches SET current_half=2,t1_empty_raids=0,t2_empty_raids=0,timer_running=0,timer_half2_sec=0 WHERE id=?",(mid,))
        # Revive ALL players for 2nd half
        conn.execute("UPDATE kabaddi_players SET is_out=0 WHERE match_id=? AND (is_bench IS NULL OR is_bench!=1)",(mid,))
        conn.execute("INSERT INTO kabaddi_events(match_id,half_no,event_type,note) VALUES(?,?,?,?)",(mid,2,'half_time','Second Half Begins — All players revived'))
        conn.commit()
    return jsonify({'success':True,'state':get_kabaddi_match_state(eid)})

@app.route('/api/kabaddi/<int:eid>/timer', methods=['POST'])
@admin_required
def api_kabaddi_timer(eid):
    import time as _time
    d = request.json
    action = d.get('action','start')
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']
    with get_db() as conn:
        half = state.get('current_half',1)
        elapsed_col = 'timer_half1_sec' if half==1 else 'timer_half2_sec'
        if action == 'start':
            conn.execute(f"UPDATE kabaddi_matches SET timer_running=1,timer_started_at=? WHERE id=?",(float(_time.time()),mid))
        elif action == 'pause':
            current_elapsed = state.get('timer_elapsed', 0) or 0
            conn.execute(f"UPDATE kabaddi_matches SET timer_running=0,{elapsed_col}=?,timer_started_at=0 WHERE id=?",(current_elapsed,mid))
        elif action == 'reset':
            conn.execute(f"UPDATE kabaddi_matches SET timer_running=0,{elapsed_col}=0,timer_started_at=0 WHERE id=?",(mid,))
        conn.commit()
    return jsonify({'success':True,'state':get_kabaddi_match_state(eid)})


@app.route('/api/kabaddi/<int:eid>/end-match', methods=['POST'])
@admin_required
def api_kabaddi_end_match(eid):
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    t1,t2 = state['team1_score'],state['team2_score']
    if t1>t2: result=f"{state['team1']} won by {t1-t2} point{'s' if t1-t2!=1 else ''}!"
    elif t2>t1: result=f"{state['team2']} won by {t2-t1} point{'s' if t2-t1!=1 else ''}!"
    else: result="Match Tied!"
    with get_db() as conn:
        conn.execute("UPDATE kabaddi_matches SET status='completed',result=? WHERE id=?",(result,state['id']))
        conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(result,eid))
        conn.commit()
    return jsonify({'success':True,'result':result})


@app.route('/api/kabaddi/<int:eid>/undo', methods=['POST'])
@admin_required
def api_kabaddi_undo(eid):
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    mid = state['id']
    with get_db() as conn:
        last = conn.execute(
            "SELECT * FROM kabaddi_events WHERE match_id=? AND event_type NOT IN ('half_time','substitution') ORDER BY id DESC LIMIT 1",
            (mid,)
        ).fetchone()
        if not last: return jsonify({'error': 'Nothing to undo'}), 400
        last = dict(last)
        raiding_team = last['raiding_team']
        defending_team = last['defending_team']
        pts_raiding = last.get('points_raiding', 0) or 0
        pts_defending = last.get('points_defending', 0) or 0
        # Reverse score on kabaddi_matches
        if raiding_team == state['team1']:
            conn.execute(
                "UPDATE kabaddi_matches SET team1_score=MAX(0,team1_score-?),team2_score=MAX(0,team2_score-?) WHERE id=?",
                (pts_raiding, pts_defending, mid)
            )
        else:
            conn.execute(
                "UPDATE kabaddi_matches SET team2_score=MAX(0,team2_score-?),team1_score=MAX(0,team1_score-?) WHERE id=?",
                (pts_raiding, pts_defending, mid)
            )
        # Restore any out players that were put out in this event
        out_players = conn.execute("SELECT * FROM kabaddi_out_players WHERE event_id=?", (last['id'],)).fetchall()
        for op in out_players:
            conn.execute(
                "UPDATE kabaddi_players SET is_out=0,out_at_raid=NULL WHERE match_id=? AND team=? AND player_name=?",
                (mid, op['team'], op['player_name'])
            )
        conn.execute("DELETE FROM kabaddi_out_players WHERE event_id=?", (last['id'],))
        conn.execute("DELETE FROM kabaddi_events WHERE id=?", (last['id'],))
        conn.commit()
    return jsonify({'success': True, 'undone': last['event_type'], 'state': get_kabaddi_match_state(eid)})


@app.route('/api/kabaddi/<int:eid>/substitute', methods=['POST'])
@admin_required
def api_kabaddi_substitute(eid):
    d = request.json
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']
    out_player = d.get('out_player','').strip()
    in_player = d.get('in_player','').strip()
    team = d.get('team','').strip()
    if not out_player or not in_player or not team:
        return jsonify({'error':'Missing out_player, in_player, or team'}),400
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM kabaddi_players WHERE match_id=? AND team=? AND player_name=?",(mid,team,out_player)).fetchone()
        if not existing: return jsonify({'error':f'Player {out_player} not found'}),404
        conn.execute("UPDATE kabaddi_players SET player_name=?,is_out=0,raid_points=0,tackle_points=0,super_tackles=0,bonus_points=0 WHERE match_id=? AND team=? AND player_name=?",(in_player,mid,team,out_player))
        conn.execute("INSERT INTO kabaddi_events(match_id,half_no,event_type,raiding_team,note) VALUES(?,?,?,?,?)",(mid,state['current_half'],'substitution',team,f'SUB: {out_player} → {in_player}'))
        conn.commit()
    return jsonify({'success':True,'state':get_kabaddi_match_state(eid)})


@app.route('/api/kabaddi/<int:eid>/revive', methods=['POST'])
@admin_required
def api_kabaddi_revive(eid):
    d = request.json
    state = get_kabaddi_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    with get_db() as conn:
        conn.execute("UPDATE kabaddi_players SET is_out=0,revivals=revivals+1 WHERE match_id=? AND player_name=? AND team=?",
            (state['id'],d['player_name'],d['team']))
        conn.commit()
    return jsonify({'success':True,'state':get_kabaddi_match_state(eid)})


# ── FOOTBALL ADMIN ─────────────────────────────────────────

@app.route('/admin/football/<int:eid>')
@admin_required
def admin_football_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order", (eid,)).fetchall()
    state = get_football_match_state(eid)
    preset = {
        'team1': {
            'main': [dict(p) for p in ep if p['team'] == 'team1' and not p['is_sub']],
            'subs': [dict(p) for p in ep if p['team'] == 'team1' and p['is_sub']],
        },
        'team2': {
            'main': [dict(p) for p in ep if p['team'] == 'team2' and not p['is_sub']],
            'subs': [dict(p) for p in ep if p['team'] == 'team2' and p['is_sub']],
        },
    }
    return render_template('admin/football_scoring.html', event=dict(ev), state=state, preset=preset)


@app.route('/admin/football/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_football(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM football_matches WHERE event_id=?",(eid,)).fetchone():
            half_dur = int(request.form.get('half_duration', 45) or 45)
            cur = conn.execute("INSERT INTO football_matches(event_id,team1,team2,status,half_duration) VALUES(?,?,?,?,?)",(eid,ev['team1'],ev['team2'],'live',half_dur))
            mid = cur.lastrowid
            for tk in ['team1','team2']:
                # Parse 11 main players with jersey numbers
                for i in range(1, 12):
                    pname = request.form.get(f'{tk}_p{i}_name','').strip()
                    pno = request.form.get(f'{tk}_p{i}_no', str(i)).strip()
                    if not pname:
                        # fallback: from event_players
                        rows = conn.execute("SELECT player_name,player_order FROM event_players WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",(eid,tk)).fetchall()
                        if rows and i <= len(rows):
                            pname = rows[i-1]['player_name']
                    if pname:
                        conn.execute("INSERT INTO football_players(match_id,team,player_name,player_no,is_sub,is_active) VALUES(?,?,?,?,0,1)",(mid,ev[tk],pname,int(pno) if pno.isdigit() else i))
                # Parse 5 sub players with jersey numbers
                for i in range(1, 6):
                    sname = request.form.get(f'{tk}_sub{i}_name','').strip()
                    sno = request.form.get(f'{tk}_sub{i}_no','').strip()
                    if not sname:
                        rows = conn.execute("SELECT player_name,player_order FROM event_players WHERE event_id=? AND team=? AND is_sub=1 ORDER BY player_order",(eid,tk)).fetchall()
                        if rows and i <= len(rows):
                            sname = rows[i-1]['player_name']
                    if sname:
                        conn.execute("INSERT INTO football_players(match_id,team,player_name,player_no,is_sub,is_active) VALUES(?,?,?,?,1,0)",(mid,ev[tk],sname,int(sno) if sno.isdigit() else 100+i))
            # Sync to event_players so preset loads on next visit
            conn.execute("DELETE FROM event_players WHERE event_id=?", (eid,))
            for tk in ['team1', 'team2']:
                for i in range(1, 12):
                    pname = request.form.get(f'{tk}_p{i}_name', '').strip()
                    pno_raw = request.form.get(f'{tk}_p{i}_no', str(i)).strip()
                    pno = int(pno_raw) if pno_raw.isdigit() else i
                    if pname:
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)", (eid, tk, pname, 'player', pno))
                for i in range(1, 6):
                    sname2 = request.form.get(f'{tk}_sub{i}_name', '').strip()
                    sno_raw2 = request.form.get(f'{tk}_sub{i}_no', '').strip()
                    sno2 = int(sno_raw2) if sno_raw2.isdigit() else 100 + i
                    if sname2:
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,1,?)", (eid, tk, sname2, 'player', sno2))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,))
            conn.commit()
    return redirect(url_for('admin_football_scoring',eid=eid))


@app.route('/api/football/<int:eid>/event', methods=['POST'])
@admin_required
def api_football_event(eid):
    import time as _time
    d = request.json; state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']
    et = d.get('event_type'); team = d.get('team'); player = d.get('player_name','')
    timer_sec = int(d.get('timer_second', state.get('timer_elapsed',0)))
    minute = timer_sec // 60
    half = int(d.get('half', state['current_half']))
    with get_db() as conn:
        conn.execute("INSERT INTO football_events(match_id,event_type,team,player_name,assist_player,minute,half,note,timer_second) VALUES(?,?,?,?,?,?,?,?,?)",
            (mid,et,team,player,d.get('assist_player',''),minute,half,d.get('note',''),timer_sec))
        if et in ('goal','penalty'):
            col = 'team1_score' if team==state['team1'] else 'team2_score'
            conn.execute(f"UPDATE football_matches SET {col}={col}+1 WHERE id=?",(mid,))
            if player: conn.execute("UPDATE football_players SET goals=goals+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
            if d.get('assist_player'): conn.execute("UPDATE football_players SET assists=assists+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,d['assist_player']))
        elif et=='own_goal':
            other = state['team2'] if team==state['team1'] else state['team1']
            col = 'team1_score' if other==state['team1'] else 'team2_score'
            conn.execute(f"UPDATE football_matches SET {col}={col}+1 WHERE id=?",(mid,))
        elif et=='yellow_card':
            conn.execute("UPDATE football_players SET yellow_cards=yellow_cards+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
            # If 2nd yellow → auto ban (sent off, cannot sub)
            ycnt = conn.execute("SELECT yellow_cards FROM football_players WHERE match_id=? AND team=? AND player_name=?",(mid,team,player)).fetchone()
            if ycnt and ycnt['yellow_cards'] >= 2:
                conn.execute("UPDATE football_players SET is_active=0,is_banned=1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
                conn.execute("INSERT INTO football_events(match_id,event_type,team,player_name,minute,half,timer_second,note) VALUES(?,?,?,?,?,?,?,?)",
                    (mid,'second_yellow',team,player,minute,half,timer_sec,'2nd Yellow — Sent Off'))
        elif et=='red_card':
            conn.execute("UPDATE football_players SET red_cards=red_cards+1,is_active=0,is_banned=1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
        conn.commit()
    return jsonify({'success':True,'state':get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/timer', methods=['POST'])
@admin_required
def api_football_timer(eid):
    import time as _time
    d = request.json; state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    action = d.get('action')
    with get_db() as conn:
        if action == 'start':
            conn.execute("UPDATE football_matches SET timer_running=1,timer_started_at=? WHERE id=?",(float(_time.time()),state['id']))
        elif action == 'pause':
            elapsed = int(_time.time() - (state.get('timer_started_at') or _time.time())) + (state.get('timer_offset') or 0)
            conn.execute("UPDATE football_matches SET timer_running=0,timer_offset=?,timer_started_at=0 WHERE id=?",(elapsed,state['id']))
        elif action == 'reset':
            conn.execute("UPDATE football_matches SET timer_running=0,timer_offset=?,timer_started_at=0 WHERE id=?",(d.get('offset',0),state['id']))
        elif action == 'set_extra':
            half = int(d.get('half',1))
            extra = int(d.get('extra_minutes',0))
            if half == 1:
                conn.execute("UPDATE football_matches SET extra_time_1=? WHERE id=?",(extra,state['id']))
            else:
                conn.execute("UPDATE football_matches SET extra_time_2=? WHERE id=?",(extra,state['id']))
        conn.commit()
    return jsonify({'success':True,'state':get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/substitute', methods=['POST'])
@admin_required
def api_football_substitute(eid):
    import time as _time
    d = request.json; state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    # Accept by jersey number OR player name
    out_jersey = d.get('out_jersey')
    out_p = d.get('out_player','').strip()
    in_jersey = d.get('in_jersey')
    in_p = d.get('in_player','').strip()
    team = d.get('team','').strip()
    timer_sec = int(d.get('timer_second', state.get('timer_elapsed',0)))
    minute = timer_sec // 60
    if not team: return jsonify({'error':'Missing team'}),400
    with get_db() as conn:
        # Find out player by jersey or name
        if out_jersey is not None:
            row = conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND player_no=?",(state['id'],team,int(out_jersey))).fetchone()
        else:
            row = conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,out_p)).fetchone()
        if not row: return jsonify({'error':'Player going out not found'}),404
        out_p = row['player_name']
        out_no = row['player_no']
        # Find in player (sub bench) by jersey or name
        if in_jersey is not None:
            in_row = conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND player_no=? AND is_sub=1",(state['id'],team,int(in_jersey))).fetchone()
            if in_row: in_p = in_row['player_name']
        if not in_p: return jsonify({'error':'Player coming in not found'}),400
        # Block banned players from subbing in
        in_player_row = conn.execute("SELECT * FROM football_players WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,in_p)).fetchone()
        if in_player_row and in_player_row['is_banned']:
            return jsonify({'error':f'{in_p} is banned (red/2nd yellow) and cannot enter the match'}),400
        # Check if player going out is banned — they cannot be swapped out normally, they're already off
        if dict(row).get('is_banned'):
            return jsonify({'error':f'{out_p} was sent off and is already out of the match'}),400
        # Activate sub player, deactivate out player
        conn.execute("UPDATE football_players SET is_active=0 WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,out_p))
        conn.execute("UPDATE football_players SET is_active=1 WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,in_p))
        conn.execute("INSERT INTO football_events(match_id,event_type,team,player_name,assist_player,minute,half,note,timer_second) VALUES(?,?,?,?,?,?,?,?,?)",
            (state['id'],'substitution',team,in_p,out_p,minute,state['current_half'],f'SUB #{out_no} {out_p} → {in_p}',timer_sec))
        conn.commit()
    return jsonify({'success':True,'state':get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/state')
def api_football_state(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error':'not found'}),404
    return jsonify(state)



@app.route('/api/football/<int:eid>/half', methods=['POST'])
@admin_required
def api_football_half(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid      = state['id']
    half     = request.json.get('half', 2)
    half_dur = state.get('half_duration', 45)

    with get_db() as conn:
        if half == 'halftime':
            # Freeze timer, mark status as halftime
            elapsed = state.get('timer_elapsed', 0)
            conn.execute(
                "UPDATE football_matches SET status='halftime',timer_running=0,timer_offset=?,timer_started_at=0 WHERE id=?",
                (elapsed, mid))
            conn.execute(
                "INSERT INTO football_events(match_id,event_type,team,minute,half,timer_second,note) VALUES(?,?,?,?,?,?,?)",
                (mid, 'halftime', '', elapsed // 60, 1, elapsed, 'Half Time'))
        elif half == 2:
            # Start 2nd half: set offset to where 2nd half begins (end of 1st half + extra1)
            extra1 = state.get('extra_time_1', 0) or 0
            offset_2nd = (half_dur + extra1) * 60
            conn.execute(
                "UPDATE football_matches SET current_half=2,status='live',timer_running=0,timer_offset=?,timer_started_at=0 WHERE id=?",
                (offset_2nd, mid))
        conn.commit()
    return jsonify({'success': True, 'state': get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/end', methods=['POST'])
@admin_required
def api_football_end(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    t1,t2 = state['team1_score'],state['team2_score']
    is_draw = (t1 == t2)
    result = f"{state['team1']} won {t1}-{t2}!" if t1>t2 else (f"{state['team2']} won {t2}-{t1}!" if t2>t1 else f"Draw {t1}-{t2}!")
    # Check if force draw (admin chose not to do penalty)
    d = request.json or {}
    force_draw = d.get('force_draw', False) or (request.args.get('force') == '1')
    if is_draw and not force_draw:
        return jsonify({'success':True,'is_draw':True,'score':f'{t1}-{t2}','result':result})
    with get_db() as conn:
        conn.execute("UPDATE football_matches SET status='completed',result=? WHERE id=?",(result,state['id']))
        conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(result,eid)); conn.commit()
    return jsonify({'success':True,'is_draw':False,'result':result})


@app.route('/api/football/<int:eid>/undo', methods=['POST'])
@admin_required
def api_football_undo(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    mid = state['id']
    with get_db() as conn:
        last = conn.execute(
            "SELECT * FROM football_events WHERE match_id=? ORDER BY id DESC LIMIT 1", (mid,)
        ).fetchone()
        if not last: return jsonify({'error': 'Nothing to undo'}), 400
        last = dict(last)
        et = last['event_type']; team = last['team']; player = last.get('player_name', '')
        # Reverse score changes
        if et in ('goal', 'penalty'):
            col = 'team1_score' if team == state['team1'] else 'team2_score'
            conn.execute(f"UPDATE football_matches SET {col}=MAX(0,{col}-1) WHERE id=?", (mid,))
            if player:
                conn.execute("UPDATE football_players SET goals=MAX(0,goals-1) WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
            assist = last.get('assist_player', '')
            if assist:
                conn.execute("UPDATE football_players SET assists=MAX(0,assists-1) WHERE match_id=? AND team=? AND player_name=?", (mid, team, assist))
        elif et == 'own_goal':
            other = state['team2'] if team == state['team1'] else state['team1']
            col = 'team1_score' if other == state['team1'] else 'team2_score'
            conn.execute(f"UPDATE football_matches SET {col}=MAX(0,{col}-1) WHERE id=?", (mid,))
        elif et == 'yellow_card':
            conn.execute("UPDATE football_players SET yellow_cards=MAX(0,yellow_cards-1) WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
            # If this was auto-generated second_yellow, also remove that
            conn.execute("DELETE FROM football_events WHERE match_id=? AND event_type='second_yellow' AND team=? AND player_name=? AND id > ?", (mid, team, player, last['id']))
            conn.execute("UPDATE football_players SET is_active=1,is_banned=0 WHERE match_id=? AND team=? AND player_name=? AND yellow_cards < 2", (mid, team, player))
        elif et == 'red_card':
            conn.execute("UPDATE football_players SET red_cards=MAX(0,red_cards-1),is_active=1,is_banned=0 WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
        conn.execute("DELETE FROM football_events WHERE id=?", (last['id'],))
        conn.commit()
    return jsonify({'success': True, 'undone': et, 'state': get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/penalty/start', methods=['POST'])
@admin_required
def api_football_penalty_start(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    d = request.json
    team1_players = d.get('team1_players', [])
    team2_players = d.get('team2_players', [])
    if len(team1_players) != 5 or len(team2_players) != 5:
        return jsonify({'error':'Must select exactly 5 players per team'}),400
    mid = state['id']
    with get_db() as conn:
        # Validate: no banned players allowed in penalty
        all_players = team1_players + team2_players
        for pname in team1_players:
            row = conn.execute("SELECT is_banned FROM football_players WHERE match_id=? AND player_name=?", (mid,pname)).fetchone()
            if row and row['is_banned']:
                return jsonify({'error':f'{pname} is banned (red/2nd yellow) and cannot take penalties'}),400
        for pname in team2_players:
            row = conn.execute("SELECT is_banned FROM football_players WHERE match_id=? AND player_name=?", (mid,pname)).fetchone()
            if row and row['is_banned']:
                return jsonify({'error':f'{pname} is banned (red/2nd yellow) and cannot take penalties'}),400
        # Mark match as penalty shootout in progress
        conn.execute("UPDATE football_matches SET status='penalty' WHERE id=?", (mid,))
        conn.execute("UPDATE events SET status='live' WHERE id=?", (eid,))
        cur = conn.execute(
            "INSERT INTO football_penalty_shootout(match_id,status,round_no) VALUES(?,?,?)",
            (mid, 'active', 1))
        sid = cur.lastrowid
        for i, pname in enumerate(team1_players):
            conn.execute(
                "INSERT INTO football_penalty_kicks(shootout_id,round_no,team,player_name,kick_order,result) VALUES(?,?,?,?,?,?)",
                (sid, 1, state['team1'], pname, i+1, 'pending'))
        for i, pname in enumerate(team2_players):
            conn.execute(
                "INSERT INTO football_penalty_kicks(shootout_id,round_no,team,player_name,kick_order,result) VALUES(?,?,?,?,?,?)",
                (sid, 1, state['team2'], pname, i+1, 'pending'))
        conn.commit()
    return jsonify({'success':True,'shootout_id':sid,'state':get_football_match_state(eid)})


@app.route('/api/football/<int:eid>/penalty/kick', methods=['POST'])
@admin_required
def api_football_penalty_kick(eid):
    state = get_football_match_state(eid)
    if not state or not state.get('penalty_shootout'): return jsonify({'error':'no shootout'}),404
    d = request.json
    kick_id = d.get('kick_id')
    result = d.get('result')  # 'scored' or 'missed'
    if result not in ('scored','missed'): return jsonify({'error':'Invalid result'}),400
    ps = state['penalty_shootout']
    sid = ps['id']
    with get_db() as conn:
        conn.execute("UPDATE football_penalty_kicks SET result=? WHERE id=? AND shootout_id=?",
                     (result, kick_id, sid))
        # Recalculate scores
        t1_scored = conn.execute(
            "SELECT COUNT(*) FROM football_penalty_kicks WHERE shootout_id=? AND team=? AND result='scored'",
            (sid, state['team1'])).fetchone()[0]
        t2_scored = conn.execute(
            "SELECT COUNT(*) FROM football_penalty_kicks WHERE shootout_id=? AND team=? AND result='scored'",
            (sid, state['team2'])).fetchone()[0]
        conn.execute("UPDATE football_penalty_shootout SET team1_score=?,team2_score=? WHERE id=?",
                     (t1_scored, t2_scored, sid))
        # Check if round is complete
        round_no = ps['round_no']
        t1_kicks = [k for k in ps['kicks'] if k['team']==state['team1'] and k['round_no']==round_no]
        t2_kicks = [k for k in ps['kicks'] if k['team']==state['team2'] and k['round_no']==round_no]
        # Update current kick result in local copy for check
        for k in t1_kicks + t2_kicks:
            if k['id'] == kick_id:
                k['result'] = result
        # Re-fetch all kicks for this round
        all_round_kicks = [dict(k) for k in conn.execute(
            "SELECT * FROM football_penalty_kicks WHERE shootout_id=? AND round_no=?",
            (sid, round_no)).fetchall()]
        t1_round = [k for k in all_round_kicks if k['team']==state['team1']]
        t2_round = [k for k in all_round_kicks if k['team']==state['team2']]
        t1_done = all(k['result']!='pending' for k in t1_round)
        t2_done = all(k['result']!='pending' for k in t2_round)
        winner = None
        next_round = False
        if t1_done and t2_done:
            t1_r = sum(1 for k in t1_round if k['result']=='scored')
            t2_r = sum(1 for k in t2_round if k['result']=='scored')
            if t1_r != t2_r:
                # Someone won
                winner = state['team1'] if t1_r > t2_r else state['team2']
                final_result = f"Penalty: {winner} wins! ({t1_scored}-{t2_scored})"
                conn.execute("UPDATE football_penalty_shootout SET status='completed',winner=? WHERE id=?",
                             (winner, sid))
                conn.execute("UPDATE football_matches SET status='completed',result=? WHERE id=?",
                             (final_result, state['id']))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?", (final_result, eid))
            else:
                # Sudden death: create new round with NEW players
                next_round = True
                new_round = round_no + 1
                conn.execute("UPDATE football_penalty_shootout SET round_no=? WHERE id=?", (new_round, sid))
        conn.commit()
    updated = get_football_match_state(eid)
    return jsonify({'success':True,'winner':winner,'next_round':next_round,'state':updated})


@app.route('/api/football/<int:eid>/penalty/new_round', methods=['POST'])
@admin_required
def api_football_penalty_new_round(eid):
    state = get_football_match_state(eid)
    if not state or not state.get('penalty_shootout'): return jsonify({'error':'no shootout'}),404
    d = request.json
    team1_players = d.get('team1_players', [])
    team2_players = d.get('team2_players', [])
    if not team1_players or not team2_players:
        return jsonify({'error':'Must provide players for next round'}),400
    ps = state['penalty_shootout']
    sid = ps['id']
    round_no = ps['round_no']
    mid = state['id']
    with get_db() as conn:
        # Collect all players who already kicked in ANY previous round
        already_kicked_t1 = set(r['player_name'] for r in conn.execute(
            "SELECT player_name FROM football_penalty_kicks WHERE shootout_id=? AND team=?",
            (sid, state['team1'])).fetchall())
        already_kicked_t2 = set(r['player_name'] for r in conn.execute(
            "SELECT player_name FROM football_penalty_kicks WHERE shootout_id=? AND team=?",
            (sid, state['team2'])).fetchall())
        # Validate: no repeat players, no banned players
        for pname in team1_players:
            if pname in already_kicked_t1:
                return jsonify({'error':f'{pname} already kicked in a previous round. Choose a different player.'}),400
            row = conn.execute("SELECT is_banned FROM football_players WHERE match_id=? AND player_name=?", (mid,pname)).fetchone()
            if row and row['is_banned']:
                return jsonify({'error':f'{pname} is banned and cannot take penalties'}),400
        for pname in team2_players:
            if pname in already_kicked_t2:
                return jsonify({'error':f'{pname} already kicked in a previous round. Choose a different player.'}),400
            row = conn.execute("SELECT is_banned FROM football_players WHERE match_id=? AND player_name=?", (mid,pname)).fetchone()
            if row and row['is_banned']:
                return jsonify({'error':f'{pname} is banned and cannot take penalties'}),400
        for i, pname in enumerate(team1_players):
            conn.execute(
                "INSERT INTO football_penalty_kicks(shootout_id,round_no,team,player_name,kick_order,result) VALUES(?,?,?,?,?,?)",
                (sid, round_no, state['team1'], pname, i+1, 'pending'))
        for i, pname in enumerate(team2_players):
            conn.execute(
                "INSERT INTO football_penalty_kicks(shootout_id,round_no,team,player_name,kick_order,result) VALUES(?,?,?,?,?,?)",
                (sid, round_no, state['team2'], pname, i+1, 'pending'))
        conn.commit()
    updated = get_football_match_state(eid)
    # Return used players so UI can block them
    already_used = {
        state['team1']: list(already_kicked_t1) + team1_players,
        state['team2']: list(already_kicked_t2) + team2_players
    }
    return jsonify({'success':True,'state':updated,'already_used':already_used})


@app.route('/api/football/<int:eid>/penalty/state')
def api_football_penalty_state(eid):
    state = get_football_match_state(eid)
    if not state: return jsonify({'error':'not found'}),404
    return jsonify(state.get('penalty_shootout'))


# ── BASKETBALL ADMIN ──────────────────────────────────────

@app.route('/admin/basketball/<int:eid>')
@admin_required
def admin_basketball_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order",(eid,)).fetchall()
    t1_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team1' and not p['is_sub'])
    t2_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team2' and not p['is_sub'])
    t1_subs = [p['player_name'] for p in ep if p['team']=='team1' and p['is_sub']]
    t2_subs = [p['player_name'] for p in ep if p['team']=='team2' and p['is_sub']]
    return render_template('admin/basketball_scoring.html',event=dict(ev),state=get_basketball_match_state(eid),
        preset_t1=t1_players, preset_t2=t2_players,
        t1_subs=t1_subs, t2_subs=t2_subs)


@app.route('/admin/basketball/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_basketball(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM basketball_matches WHERE event_id=?",(eid,)).fetchone():
            cur = conn.execute("INSERT INTO basketball_matches(event_id,team1,team2,status) VALUES(?,?,?,?)",(eid,ev['team1'],ev['team2'],'live'))
            mid = cur.lastrowid
            conn.execute("INSERT INTO basketball_quarters(match_id,quarter_no) VALUES(?,?)",(mid,1))
            for tk in ['team1','team2']:
                plist = [x.strip() for x in request.form.get(f'{tk}_players','').split('\n') if x.strip()]
                if not plist:
                    plist = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",(eid,tk)).fetchall()]
                for i,p in enumerate(plist):
                    conn.execute("INSERT INTO basketball_players(match_id,team,player_name,player_no) VALUES(?,?,?,?)",(mid,ev[tk],p,i+1))
                # Save subs from start form into event_players (upsert pattern)
                subs_raw = [x.strip() for x in request.form.get(f'{tk}_subs','').split('\n') if x.strip()]
                if subs_raw:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=1",(eid,tk))
                    for i,s in enumerate(subs_raw):
                        role = request.form.get(f'{tk}_sub{i+1}_role','player')
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,1,?)",(eid,tk,s,role,i+1))
                # Also save main players so event_players is in sync
                if plist:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=0",(eid,tk))
                    for i,p in enumerate(plist):
                        role = request.form.get(f'{tk}_p{i+1}_role','player')
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)",(eid,tk,p,role,i+1))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,)); conn.commit()
    return redirect(url_for('admin_basketball_scoring',eid=eid))


@app.route('/api/basketball/<int:eid>/state')
def api_basketball_state(eid):
    state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    return jsonify({'team1_score':state['team1_score'],'team2_score':state['team2_score'],'status':state['status'],'current_quarter':state.get('current_quarter',1)})


@app.route('/api/volleyball/<int:eid>/state')
def api_volleyball_state(eid):
    state = get_volleyball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    return jsonify({'team1_sets':state['team1_sets'],'team2_sets':state['team2_sets'],'status':state['status'],'current_set':state.get('current_set',1)})


@app.route('/api/badminton/<int:eid>/state')
def api_badminton_state(eid):
    state = get_badminton_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    return jsonify(state)


@app.route('/api/basketball/<int:eid>/score', methods=['POST'])
@admin_required
def api_basketball_score(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    mid = state['id']; team = d.get('team'); pts = int(d.get('points',2)); q = state['current_quarter']
    with get_db() as conn:
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        conn.execute(f"UPDATE basketball_matches SET {col}={col}+? WHERE id=?",(pts,mid))
        conn.execute(f"UPDATE basketball_quarters SET {col}={col}+? WHERE match_id=? AND quarter_no=?",(pts,mid,q))
        p = d.get('player_name','')
        if p: conn.execute("UPDATE basketball_players SET points=points+? WHERE match_id=? AND team=? AND player_name=?",(pts,mid,team,p))
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,points,quarter) VALUES(?,?,?,?,?,?)",(mid,f'{pts}pt',team,p,pts,q))
        conn.commit()
    return jsonify({'success':True,'state':get_basketball_match_state(eid)})


@app.route('/api/basketball/<int:eid>/substitute', methods=['POST'])
@admin_required
def api_basketball_substitute(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    out_p = d.get('out_player','').strip(); in_p = d.get('in_player','').strip(); team = d.get('team','').strip()
    if not out_p or not in_p or not team: return jsonify({'error':'Missing fields'}),400
    with get_db() as conn:
        if not conn.execute("SELECT id FROM basketball_players WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,out_p)).fetchone():
            return jsonify({'error':f'{out_p} not found'}),404
        # Preserve the out player's fouls/stats in a log, reset for the sub coming in
        conn.execute("UPDATE basketball_players SET player_name=?,points=0,rebounds=0,assists=0,steals=0,blocks=0,fouls=0,technical_fouls=0,is_fouled_out=0,is_ejected=0 WHERE match_id=? AND team=? AND player_name=?",(in_p,state['id'],team,out_p))
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,quarter,note) VALUES(?,?,?,?,?,?)",(state['id'],'substitution',team,in_p,state['current_quarter'],f'SUB: {out_p} OUT / {in_p} IN'))
        conn.commit()
    return jsonify({'success':True,'state':get_basketball_match_state(eid)})


@app.route('/api/basketball/<int:eid>/foul', methods=['POST'])
@admin_required
def api_basketball_foul(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    team = d.get('team',''); player = d.get('player_name','')
    foul_type = d.get('foul_type','personal')
    mid = state['id']; q = state['current_quarter']
    foul_col = 'team1_fouls' if team == state['team1'] else 'team2_fouls'
    alerts = []
    with get_db() as conn:
        if foul_type == 'technical':
            conn.execute("UPDATE basketball_players SET technical_fouls=technical_fouls+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
            p = conn.execute("SELECT technical_fouls,is_ejected FROM basketball_players WHERE match_id=? AND team=? AND player_name=?",(mid,team,player)).fetchone()
            if p and p['technical_fouls'] >= 2 and not p['is_ejected']:
                conn.execute("UPDATE basketball_players SET is_ejected=1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
                alerts.append(f"EJECTED: {player} (2 technical fouls)")
            note = f'Technical foul on {player}'
        elif foul_type == 'flagrant':
            conn.execute("UPDATE basketball_players SET fouls=fouls+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
            conn.execute(f"UPDATE basketball_quarters SET {foul_col}={foul_col}+1 WHERE match_id=? AND quarter_no=?",(mid,q))
            note = f'Flagrant foul on {player}'
        else:
            conn.execute("UPDATE basketball_players SET fouls=fouls+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
            conn.execute(f"UPDATE basketball_quarters SET {foul_col}={foul_col}+1 WHERE match_id=? AND quarter_no=?",(mid,q))
            p = conn.execute("SELECT fouls,is_fouled_out FROM basketball_players WHERE match_id=? AND team=? AND player_name=?",(mid,team,player)).fetchone()
            if p:
                if p['fouls'] >= 4 and not p['is_fouled_out']:
                    alerts.append(f"WARNING: {player} has {p['fouls']} fouls")
                if p['fouls'] >= 5 and not p['is_fouled_out']:
                    conn.execute("UPDATE basketball_players SET is_fouled_out=1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,player))
                    alerts.append(f"FOULED OUT: {player}")
            note = None
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,quarter,note) VALUES(?,?,?,?,?,?)",(mid,foul_type+'_foul',team,player,q,note))
        conn.commit()
    new_state = get_basketball_match_state(eid)
    if team == state['team1'] and new_state['team2_in_bonus'] and not state['team2_in_bonus']:
        alerts.append(f"BONUS: {state['team2']} are now in the bonus!")
    if team == state['team2'] and new_state['team1_in_bonus'] and not state['team1_in_bonus']:
        alerts.append(f"BONUS: {state['team1']} are now in the bonus!")
    return jsonify({'success':True, 'alerts': alerts, 'state': new_state})


@app.route('/api/basketball/<int:eid>/and1', methods=['POST'])
@admin_required
def api_basketball_and1(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    scoring_team = d.get('scoring_team',''); pts = int(d.get('points',2))
    scoring_player = d.get('scoring_player','')
    fouled_player = d.get('fouled_player','')
    fouled_team = state['team2'] if scoring_team == state['team1'] else state['team1']
    mid = state['id']; q = state['current_quarter']
    foul_col = 'team1_fouls' if fouled_team == state['team1'] else 'team2_fouls'
    score_col = 'team1_score' if scoring_team == state['team1'] else 'team2_score'
    with get_db() as conn:
        conn.execute(f"UPDATE basketball_matches SET {score_col}={score_col}+? WHERE id=?",(pts,mid))
        conn.execute(f"UPDATE basketball_quarters SET {score_col}={score_col}+?,{foul_col}={foul_col}+1 WHERE match_id=? AND quarter_no=?",(pts,mid,q))
        if scoring_player:
            conn.execute("UPDATE basketball_players SET points=points+? WHERE match_id=? AND team=? AND player_name=?",(pts,mid,scoring_team,scoring_player))
        if fouled_player:
            conn.execute("UPDATE basketball_players SET fouls=fouls+1 WHERE match_id=? AND team=? AND player_name=?",(mid,fouled_team,fouled_player))
            p = conn.execute("SELECT fouls FROM basketball_players WHERE match_id=? AND team=? AND player_name=?",(mid,fouled_team,fouled_player)).fetchone()
            if p and p['fouls'] >= 5:
                conn.execute("UPDATE basketball_players SET is_fouled_out=1 WHERE match_id=? AND team=? AND player_name=?",(mid,fouled_team,fouled_player))
        note = f'And-1: {scoring_player} scores {pts}pts fouled by {fouled_player}'
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,points,quarter,note) VALUES(?,?,?,?,?,?,?)",(mid,'and1',scoring_team,scoring_player,pts,q,note))
        conn.commit()
    return jsonify({'success':True,'state':get_basketball_match_state(eid),'note':f'And-1! +{pts}pts + 1 free throw awarded'})


@app.route('/api/basketball/<int:eid>/goaltending', methods=['POST'])
@admin_required
def api_basketball_goaltending(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    team = d.get('team',''); pts = int(d.get('points',2)); player = d.get('player_name','')
    mid = state['id']; q = state['current_quarter']
    score_col = 'team1_score' if team == state['team1'] else 'team2_score'
    with get_db() as conn:
        conn.execute(f"UPDATE basketball_matches SET {score_col}={score_col}+? WHERE id=?",(pts,mid))
        conn.execute(f"UPDATE basketball_quarters SET {score_col}={score_col}+? WHERE match_id=? AND quarter_no=?",(pts,mid,q))
        if player:
            conn.execute("UPDATE basketball_players SET points=points+? WHERE match_id=? AND team=? AND player_name=?",(pts,mid,team,player))
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,points,quarter,note) VALUES(?,?,?,?,?,?,?)",(mid,'goaltending',team,player,pts,q,f'Goaltending - {pts}pts awarded'))
        conn.commit()
    return jsonify({'success':True,'state':get_basketball_match_state(eid)})


@app.route('/api/basketball/<int:eid>/owngoal', methods=['POST'])
@admin_required
def api_basketball_owngoal(eid):
    d = request.json; state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    own_team = d.get('team','')
    credited_team = state['team2'] if own_team == state['team1'] else state['team1']
    mid = state['id']; q = state['current_quarter']
    score_col = 'team1_score' if credited_team == state['team1'] else 'team2_score'
    with get_db() as conn:
        conn.execute(f"UPDATE basketball_matches SET {score_col}={score_col}+2 WHERE id=?",(mid,))
        conn.execute(f"UPDATE basketball_quarters SET {score_col}={score_col}+2 WHERE match_id=? AND quarter_no=?",(mid,q))
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,player_name,points,quarter,note) VALUES(?,?,?,?,?,?,?)",(mid,'own_goal',credited_team,'',2,q,f'Own goal by {own_team} - 2pts to {credited_team}'))
        conn.commit()
    return jsonify({'success':True,'state':get_basketball_match_state(eid),'note':f'Own goal! 2pts to {credited_team}'})


@app.route('/api/basketball/<int:eid>/shot-clock-reset', methods=['POST'])
@admin_required
def api_basketball_shot_clock(eid):
    state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    d = request.json; team = d.get('team','')
    with get_db() as conn:
        conn.execute("INSERT INTO basketball_events(match_id,event_type,team,quarter,note) VALUES(?,?,?,?,?)",(state['id'],'shot_clock',team,state['current_quarter'],f'Shot clock violation - {team} turnover'))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/basketball/<int:eid>/next-quarter', methods=['POST'])
@admin_required
def api_basketball_next_quarter(eid):
    state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    nq = state['current_quarter']+1
    with get_db() as conn:
        conn.execute("UPDATE basketball_matches SET current_quarter=? WHERE id=?",(nq,state['id']))
        conn.execute("INSERT INTO basketball_quarters(match_id,quarter_no) VALUES(?,?)",(state['id'],nq)); conn.commit()
    return jsonify({'success':True,'quarter':nq})


@app.route('/api/basketball/<int:eid>/end', methods=['POST'])
@admin_required
def api_basketball_end(eid):
    state = get_basketball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    t1,t2 = state['team1_score'],state['team2_score']
    result = f"{state['team1']} won {t1}-{t2}!" if t1>t2 else (f"{state['team2']} won {t2}-{t1}!" if t2>t1 else "Overtime needed!")
    with get_db() as conn:
        conn.execute("UPDATE basketball_matches SET status='completed',result=? WHERE id=?",(result,state['id']))
        conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(result,eid)); conn.commit()
    return jsonify({'success':True,'result':result})


@app.route('/api/basketball/<int:eid>/undo', methods=['POST'])
@admin_required
def api_basketball_undo(eid):
    state = get_basketball_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    mid = state['id']
    with get_db() as conn:
        last = conn.execute(
            "SELECT * FROM basketball_events WHERE match_id=? ORDER BY id DESC LIMIT 1", (mid,)
        ).fetchone()
        if not last: return jsonify({'error': 'Nothing to undo'}), 400
        last = dict(last)
        et = last['event_type']; team = last['team']; player = last.get('player_name', '')
        q = last.get('quarter', state['current_quarter'])
        if et.endswith('pt'):
            pts = last.get('points', 0)
            col = 'team1_score' if team == state['team1'] else 'team2_score'
            conn.execute(f"UPDATE basketball_matches SET {col}=MAX(0,{col}-?) WHERE id=?", (pts, mid))
            conn.execute(f"UPDATE basketball_quarters SET {col}=MAX(0,{col}-?) WHERE match_id=? AND quarter_no=?", (pts, mid, q))
            if player:
                conn.execute("UPDATE basketball_players SET points=MAX(0,points-?) WHERE match_id=? AND team=? AND player_name=?", (pts, mid, team, player))
        elif et in ('personal_foul', 'technical_foul', 'flagrant_foul'):
            foul_col = 'team1_fouls' if team == state['team1'] else 'team2_fouls'
            if et == 'technical_foul':
                conn.execute("UPDATE basketball_players SET technical_fouls=MAX(0,technical_fouls-1) WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
            else:
                conn.execute("UPDATE basketball_players SET fouls=MAX(0,fouls-1),is_fouled_out=0 WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
                conn.execute(f"UPDATE basketball_quarters SET {foul_col}=MAX(0,{foul_col}-1) WHERE match_id=? AND quarter_no=?", (mid, q))
        elif et == 'and1':
            col = 'team1_score' if team == state['team1'] else 'team2_score'
            conn.execute(f"UPDATE basketball_matches SET {col}=MAX(0,{col}-1) WHERE id=?", (mid,))
            conn.execute(f"UPDATE basketball_quarters SET {col}=MAX(0,{col}-1) WHERE match_id=? AND quarter_no=?", (mid, q))
            if player:
                conn.execute("UPDATE basketball_players SET points=MAX(0,points-1) WHERE match_id=? AND team=? AND player_name=?", (mid, team, player))
        elif et == 'own_goal':
            other = state['team2'] if team == state['team1'] else state['team1']
            col = 'team1_score' if other == state['team1'] else 'team2_score'
            conn.execute(f"UPDATE basketball_matches SET {col}=MAX(0,{col}-2) WHERE id=?", (mid,))
            conn.execute(f"UPDATE basketball_quarters SET {col}=MAX(0,{col}-2) WHERE match_id=? AND quarter_no=?", (mid, q))
        conn.execute("DELETE FROM basketball_events WHERE id=?", (last['id'],))
        conn.commit()
    return jsonify({'success': True, 'undone': et, 'state': get_basketball_match_state(eid)})


# ── VOLLEYBALL ADMIN ──────────────────────────────────────

@app.route('/admin/volleyball/<int:eid>')
@admin_required
def admin_volleyball_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order",(eid,)).fetchall()
    t1_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team1' and not p['is_sub'])
    t2_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team2' and not p['is_sub'])
    t1_subs = [p['player_name'] for p in ep if p['team']=='team1' and p['is_sub']]
    t2_subs = [p['player_name'] for p in ep if p['team']=='team2' and p['is_sub']]
    return render_template('admin/volleyball_scoring.html',event=dict(ev),state=get_volleyball_match_state(eid),
        preset_t1=t1_players, preset_t2=t2_players,
        t1_subs=t1_subs, t2_subs=t2_subs)


@app.route('/admin/volleyball/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_volleyball(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM volleyball_matches WHERE event_id=?",(eid,)).fetchone():
            cur = conn.execute("INSERT INTO volleyball_matches(event_id,team1,team2,status) VALUES(?,?,?,?)",(eid,ev['team1'],ev['team2'],'live'))
            mid = cur.lastrowid
            conn.execute("INSERT INTO volleyball_sets(match_id,set_no,status) VALUES(?,?,?)",(mid,1,'active'))
            for tk in ['team1','team2']:
                plist = [x.strip() for x in request.form.get(f'{tk}_players','').split('\n') if x.strip()]
                if not plist:
                    plist = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",(eid,tk)).fetchall()]
                for i,p in enumerate(plist):
                    conn.execute("INSERT INTO volleyball_players(match_id,team,player_name,player_no) VALUES(?,?,?,?)",(mid,ev[tk],p,i+1))
                subs_raw = [x.strip() for x in request.form.get(f'{tk}_subs','').split('\n') if x.strip()]
                if subs_raw:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=1",(eid,tk))
                    for i,s in enumerate(subs_raw):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,1,?)",(eid,tk,s,'player',i+1))
                if plist:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=0",(eid,tk))
                    for i,p in enumerate(plist):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)",(eid,tk,p,'player',i+1))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,)); conn.commit()
    return redirect(url_for('admin_volleyball_scoring',eid=eid))


@app.route('/api/volleyball/<int:eid>/substitute', methods=['POST'])
@admin_required
def api_volleyball_substitute(eid):
    d = request.json; state = get_volleyball_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    out_p = d.get('out_player','').strip(); in_p = d.get('in_player','').strip(); team = d.get('team','').strip()
    if not out_p or not in_p or not team: return jsonify({'error':'Missing fields'}),400
    with get_db() as conn:
        if not conn.execute("SELECT id FROM volleyball_players WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,out_p)).fetchone():
            return jsonify({'error':f'{out_p} not found'}),404
        conn.execute("UPDATE volleyball_players SET player_name=?,spikes=0,blocks=0,aces=0,digs=0 WHERE match_id=? AND team=? AND player_name=?",(in_p,state['id'],team,out_p))
        conn.commit()
    return jsonify({'success':True,'state':get_volleyball_match_state(eid)})


@app.route('/api/volleyball/<int:eid>/point', methods=['POST'])
@admin_required
def api_volleyball_point(eid):
    d = request.json; state = get_volleyball_match_state(eid)
    if not state or not state['current_set']: return jsonify({'error':'no active set'}),400
    mid = state['id']; team = d.get('team'); cs = state['current_set']; p = d.get('player_name','')
    pt = d.get('point_type','point')
    with get_db() as conn:
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        conn.execute(f"UPDATE volleyball_sets SET {col}={col}+1 WHERE id=?",(cs['id'],))
        if p:
            sc = {'spike':'spikes','block':'blocks','ace':'aces','dig':'digs'}.get(pt)
            if sc: conn.execute(f"UPDATE volleyball_players SET {sc}={sc}+1 WHERE match_id=? AND team=? AND player_name=?",(mid,team,p))
        us = dict(conn.execute("SELECT * FROM volleyball_sets WHERE id=?",(cs['id'],)).fetchone())
        t1s,t2s = us['team1_score'],us['team2_score']
        ws = 15 if cs['set_no']==5 else 25
        sw = None
        if t1s>=ws and t1s-t2s>=2: sw=state['team1']
        elif t2s>=ws and t2s-t1s>=2: sw=state['team2']
        set_over=False; match_over=False; match_result=None
        if sw:
            set_over=True
            conn.execute("UPDATE volleyball_sets SET winner=?,status='completed' WHERE id=?",(sw,cs['id']))
            sc2='team1_sets' if sw==state['team1'] else 'team2_sets'
            conn.execute(f"UPDATE volleyball_matches SET {sc2}={sc2}+1 WHERE id=?",(mid,))
            um = dict(conn.execute("SELECT * FROM volleyball_matches WHERE id=?",(mid,)).fetchone())
            if um['team1_sets']>=3: match_over=True; match_result=f"{state['team1']} won {um['team1_sets']}-{um['team2_sets']}!"
            elif um['team2_sets']>=3: match_over=True; match_result=f"{state['team2']} won {um['team2_sets']}-{um['team1_sets']}!"
            else:
                ns=cs['set_no']+1
                conn.execute("INSERT INTO volleyball_sets(match_id,set_no,status) VALUES(?,?,?)",(mid,ns,'active'))
                conn.execute("UPDATE volleyball_matches SET current_set=? WHERE id=?",(ns,mid))
            if match_over:
                conn.execute("UPDATE volleyball_matches SET status='completed',result=? WHERE id=?",(match_result,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(match_result,eid))
        conn.commit()
    return jsonify({'success':True,'set_over':set_over,'set_winner':sw,'match_over':match_over,'match_result':match_result,'state':get_volleyball_match_state(eid)})


@app.route('/api/volleyball/<int:eid>/undo', methods=['POST'])
@admin_required
def api_volleyball_undo(eid):
    state = get_volleyball_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    mid = state['id']
    cs = state.get('current_set')
    if not cs: return jsonify({'error': 'No active set'}), 400
    d = request.json or {}
    team = d.get('team', '')
    with get_db() as conn:
        cur_set = dict(conn.execute("SELECT * FROM volleyball_sets WHERE id=?", (cs['id'],)).fetchone())
        if cur_set['team1_score'] == 0 and cur_set['team2_score'] == 0:
            return jsonify({'error': 'Nothing to undo'}), 400
        if team == state['team1']:
            if cur_set['team1_score'] == 0: return jsonify({'error': 'No points to undo for this team'}), 400
            col = 'team1_score'
        elif team == state['team2']:
            if cur_set['team2_score'] == 0: return jsonify({'error': 'No points to undo for this team'}), 400
            col = 'team2_score'
        else:
            # No team specified — undo whichever team scored last (higher score heuristic)
            if cur_set['team1_score'] >= cur_set['team2_score'] and cur_set['team1_score'] > 0:
                col = 'team1_score'; team = state['team1']
            else:
                col = 'team2_score'; team = state['team2']
        conn.execute(f"UPDATE volleyball_sets SET {col}=MAX(0,{col}-1) WHERE id=?", (cs['id'],))
        conn.commit()
    return jsonify({'success': True, 'state': get_volleyball_match_state(eid)})


# ── BADMINTON ADMIN ──────────────────────────────────────

@app.route('/admin/badminton/<int:eid>')
@admin_required
def admin_badminton_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order",(eid,)).fetchall()
    t1_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team1' and not p['is_sub'])
    t2_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team2' and not p['is_sub'])
    t1_subs = [p['player_name'] for p in ep if p['team']=='team1' and p['is_sub']]
    t2_subs = [p['player_name'] for p in ep if p['team']=='team2' and p['is_sub']]
    return render_template('admin/badminton_scoring.html',event=dict(ev),state=get_badminton_match_state(eid),
        preset_t1=t1_players, preset_t2=t2_players,
        t1_subs=t1_subs, t2_subs=t2_subs)


@app.route('/admin/badminton/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_badminton(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM badminton_matches WHERE event_id=?",(eid,)).fetchone():
            match_type = request.form.get('match_type','best_of_3')
            player_mode = request.form.get('player_mode','singles')
            cur = conn.execute("INSERT INTO badminton_matches(event_id,team1,team2,match_type,player_mode,status) VALUES(?,?,?,?,?,?)",
                (eid,ev['team1'],ev['team2'],match_type,player_mode,'live'))
            mid = cur.lastrowid
            conn.execute("INSERT INTO badminton_games(match_id,game_no,status) VALUES(?,?,?)",(mid,1,'active'))
            for tk in ['team1','team2']:
                plist = [x.strip() for x in request.form.get(f'{tk}_players','').split('\n') if x.strip()]
                if not plist:
                    plist = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",(eid,tk)).fetchall()]
                for p in plist:
                    conn.execute("INSERT INTO badminton_players(match_id,team,player_name) VALUES(?,?,?)",(mid,ev[tk],p))
                if plist:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=0",(eid,tk))
                    for i,p in enumerate(plist):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)",(eid,tk,p,'player',i+1))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,)); conn.commit()
    return redirect(url_for('admin_badminton_scoring',eid=eid))


@app.route('/api/badminton/<int:eid>/point', methods=['POST'])
@admin_required
def api_badminton_point(eid):
    d = request.json; state = get_badminton_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    mid = state['id']; team = d.get('team'); cg = state['current_game']
    shot_type = d.get('shot_type','rally'); server = d.get('server','')
    if not team: return jsonify({'error':'team required'}),400
    with get_db() as conn:
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        opp_col = 'team2_score' if team==state['team1'] else 'team1_score'
        # Update streak counters
        if team==state['team1']:
            conn.execute("UPDATE badminton_games SET t1_streak=t1_streak+1, t2_streak=0 WHERE id=?",(cg['id'],))
        else:
            conn.execute("UPDATE badminton_games SET t2_streak=t2_streak+1, t1_streak=0 WHERE id=?",(cg['id'],))
        # Increment score, rally count and update server (winner serves next)
        conn.execute(f"UPDATE badminton_games SET {col}={col}+1, rally_count=rally_count+1, server=? WHERE id=?",(team,cg['id']))
        ug = dict(conn.execute("SELECT * FROM badminton_games WHERE id=?",(cg['id'],)).fetchone())
        t1s,t2s = ug['team1_score'],ug['team2_score']
        # Update shot stats for player (safe update that handles missing points_won column)
        try:
            conn.execute("UPDATE badminton_players SET points_won=points_won+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
        except Exception:
            pass
        if shot_type in ('smash','net_kill','drop','unforced_error'):
            stat_col = {'smash':'smashes','net_kill':'net_kills','drop':'drops','unforced_error':'unforced_errors'}.get(shot_type)
            if stat_col:
                try:
                    conn.execute(f"UPDATE badminton_players SET {stat_col}={stat_col}+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
                except Exception:
                    pass
        # Log point
        conn.execute("INSERT INTO badminton_points(match_id,game_id,team,shot_type,t1_score_after,t2_score_after,server) VALUES(?,?,?,?,?,?,?)",
                     (mid,cg['id'],team,shot_type,t1s,t2s,team))
        # Check game win: BWF rules — first to 21, win by 2, max 30
        gw = None
        if t1s >= 21 and t1s - t2s >= 2: gw = state['team1']
        elif t2s >= 21 and t2s - t1s >= 2: gw = state['team2']
        elif t1s >= 30: gw = state['team1']   # 30-29 win
        elif t2s >= 30: gw = state['team2']   # 30-29 win
        go=False; mo=False; mr=None
        if gw:
            go=True
            conn.execute("UPDATE badminton_games SET winner=?,status='completed' WHERE id=?",(gw,cg['id']))
            gc='team1_games' if gw==state['team1'] else 'team2_games'
            conn.execute(f"UPDATE badminton_matches SET {gc}={gc}+1 WHERE id=?",(mid,))
            um = dict(conn.execute("SELECT * FROM badminton_matches WHERE id=?",(mid,)).fetchone())
            wn = 1 if um['match_type']=='single' else (2 if um['match_type']=='best_of_3' else 3)
            if um['team1_games']>=wn: mo=True; mr=f"{state['team1']} won {um['team1_games']}-{um['team2_games']}!"
            elif um['team2_games']>=wn: mo=True; mr=f"{state['team2']} won {um['team2_games']}-{um['team1_games']}!"
            else:
                ng=cg['game_no']+1
                conn.execute("INSERT INTO badminton_games(match_id,game_no,status,server) VALUES(?,?,?,?)",(mid,ng,'active',gw))
                conn.execute("UPDATE badminton_matches SET current_game=? WHERE id=?",(ng,mid))
            if mo:
                conn.execute("UPDATE badminton_matches SET status='completed',result=? WHERE id=?",(mr,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(mr,eid))
        conn.commit()
    return jsonify({'success':True,'game_over':go,'game_winner':gw,'match_over':mo,'match_result':mr,'state':get_badminton_match_state(eid)})


@app.route('/api/badminton/<int:eid>/undo', methods=['POST'])
@admin_required
def api_badminton_undo(eid):
    state = get_badminton_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    mid = state['id']; cg = state['current_game']
    with get_db() as conn:
        last = conn.execute("SELECT * FROM badminton_points WHERE match_id=? AND game_id=? ORDER BY id DESC LIMIT 1",(mid,cg['id'])).fetchone()
        if not last: return jsonify({'error':'Nothing to undo'}),400
        last = dict(last)
        team = last['team']
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        conn.execute(f"UPDATE badminton_games SET {col}=MAX(0,{col}-1), rally_count=MAX(0,rally_count-1) WHERE id=?",(cg['id'],))
        # Revert streak (set to previous values stored in point log)
        conn.execute("DELETE FROM badminton_points WHERE id=?",(last['id'],))
        # Revert shot stat
        shot_type = last.get('shot_type','rally')
        if shot_type in ('smash','net_kill','drop','unforced_error'):
            stat_col = {'smash':'smashes','net_kill':'net_kills','drop':'drops','unforced_error':'unforced_errors'}.get(shot_type)
            if stat_col:
                try:
                    conn.execute(f"UPDATE badminton_players SET {stat_col}=MAX(0,{stat_col}-1) WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
                except Exception:
                    pass
        conn.commit()
    return jsonify({'success':True,'state':get_badminton_match_state(eid)})


@app.route('/api/badminton/<int:eid>/fault', methods=['POST'])
@admin_required
def api_badminton_fault(eid):
    """Record a fault (awards point to other team)."""
    d = request.json; state = get_badminton_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    faulting_team = d.get('team'); fault_type = d.get('fault_type','service_fault')
    if not faulting_team: return jsonify({'error':'team required'}),400
    # A fault gives point to the OTHER team
    other_team = state['team2'] if faulting_team==state['team1'] else state['team1']
    mid = state['id']; cg = state['current_game']
    with get_db() as conn:
        if fault_type == 'service_fault':
            try:
                conn.execute("UPDATE badminton_players SET service_faults=service_faults+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,faulting_team))
            except Exception:
                pass
        col = 'team1_score' if other_team==state['team1'] else 'team2_score'
        if faulting_team==state['team1']:
            conn.execute("UPDATE badminton_games SET t2_streak=t2_streak+1, t1_streak=0 WHERE id=?",(cg['id'],))
        else:
            conn.execute("UPDATE badminton_games SET t1_streak=t1_streak+1, t2_streak=0 WHERE id=?",(cg['id'],))
        conn.execute(f"UPDATE badminton_games SET {col}={col}+1, rally_count=rally_count+1, server=? WHERE id=?",(other_team,cg['id']))
        ug = dict(conn.execute("SELECT * FROM badminton_games WHERE id=?",(cg['id'],)).fetchone())
        t1s,t2s = ug['team1_score'],ug['team2_score']
        try:
            conn.execute("UPDATE badminton_players SET points_won=points_won+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,other_team))
        except Exception:
            pass
        conn.execute("INSERT INTO badminton_points(match_id,game_id,team,shot_type,fault_type,t1_score_after,t2_score_after,server) VALUES(?,?,?,?,?,?,?,?)",
                     (mid,cg['id'],other_team,'fault',fault_type,t1s,t2s,other_team))
        gw = None
        if t1s>=21 and t1s-t2s>=2: gw=state['team1']
        elif t2s>=21 and t2s-t1s>=2: gw=state['team2']
        elif t1s>=30: gw=state['team1']
        elif t2s>=30: gw=state['team2']
        go=False; mo=False; mr=None
        if gw:
            go=True
            conn.execute("UPDATE badminton_games SET winner=?,status='completed' WHERE id=?",(gw,cg['id']))
            gc='team1_games' if gw==state['team1'] else 'team2_games'
            conn.execute(f"UPDATE badminton_matches SET {gc}={gc}+1 WHERE id=?",(mid,))
            um = dict(conn.execute("SELECT * FROM badminton_matches WHERE id=?",(mid,)).fetchone())
            wn = 1 if um['match_type']=='single' else (2 if um['match_type']=='best_of_3' else 3)
            if um['team1_games']>=wn: mo=True; mr=f"{state['team1']} won {um['team1_games']}-{um['team2_games']}!"
            elif um['team2_games']>=wn: mo=True; mr=f"{state['team2']} won {um['team2_games']}-{um['team1_games']}!"
            else:
                ng=cg['game_no']+1
                conn.execute("INSERT INTO badminton_games(match_id,game_no,status,server) VALUES(?,?,?,?)",(mid,ng,'active',gw))
                conn.execute("UPDATE badminton_matches SET current_game=? WHERE id=?",(ng,mid))
            if mo:
                conn.execute("UPDATE badminton_matches SET status='completed',result=? WHERE id=?",(mr,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(mr,eid))
        conn.commit()
    return jsonify({'success':True,'game_over':go,'game_winner':gw,'match_over':mo,'match_result':mr,'state':get_badminton_match_state(eid)})


@app.route('/api/badminton/<int:eid>/card', methods=['POST'])
@admin_required
def api_badminton_card(eid):
    d = request.json; state = get_badminton_match_state(eid)
    if not state: return jsonify({'error':'no match'}),400
    mid = state['id']; cg = state.get('current_game')
    with get_db() as conn:
        conn.execute("INSERT INTO badminton_cards(match_id,game_id,team,player,card_type,reason) VALUES(?,?,?,?,?,?)",
                     (mid, cg['id'] if cg else None, d.get('team'), d.get('player'), d.get('card_type','yellow'), d.get('reason','')))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/badminton/<int:eid>/server', methods=['POST'])
@admin_required
def api_badminton_set_server(eid):
    """Update who is serving without adding a point."""
    d = request.json; state = get_badminton_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    server = d.get('server','')
    cg = state['current_game']
    with get_db() as conn:
        conn.execute("UPDATE badminton_games SET server=? WHERE id=?",(server,cg['id']))
        conn.commit()
    return jsonify({'success':True,'server':server,'state':get_badminton_match_state(eid)})


@app.route('/api/badminton/<int:eid>/stats')
@admin_required
def api_badminton_stats(eid):
    state = get_badminton_match_state(eid)
    if not state: return jsonify({'error':'no match'}),400
    mid = state['id']
    with get_db() as conn:
        points = [dict(p) for p in conn.execute("SELECT * FROM badminton_points WHERE match_id=? ORDER BY id",(mid,)).fetchall()]
        cards = [dict(c) for c in conn.execute("SELECT * FROM badminton_cards WHERE match_id=? ORDER BY id",(mid,)).fetchall()]
        players = [dict(p) for p in conn.execute("SELECT * FROM badminton_players WHERE match_id=? ORDER BY team,player_name",(mid,)).fetchall()]
    return jsonify({'success':True,'points':points,'cards':cards,'players':players,'state':state})


# ── CRICKET API ──────────────────────────────────────────

@app.route('/api/match/new', methods=['POST'])
def api_new_match():
    d=request.json
    team1,team2=d['team1'].strip(),d['team2'].strip()
    total_overs=int(d['total_overs'])
    toss_winner=d['toss_winner'].strip()
    batting_first=d['batting_first'].strip()
    bowling_first=team2 if batting_first==team1 else team1
    t1p=[p.strip() for p in d['team1_players'] if p.strip()]
    t2p=[p.strip() for p in d['team2_players'] if p.strip()]
    t1_structured=d.get('team1_players_structured')
    t2_structured=d.get('team2_players_structured')
    with get_db() as conn:
        mid=d.get('match_id'); event_id=d.get('event_id')
        if mid:
            conn.execute("UPDATE cricket_matches SET toss_winner=?,batting_first=?,status='live' WHERE id=?",(toss_winner,batting_first,mid))
        else:
            cur=conn.execute("INSERT INTO cricket_matches(event_id,team1,team2,total_overs,toss_winner,batting_first,status) VALUES(?,?,?,?,?,?,?)",(event_id,team1,team2,total_overs,toss_winner,batting_first,'live'))
            mid=cur.lastrowid
        cur2=conn.execute("INSERT INTO cricket_innings(match_id,inning_no,batting_team,bowling_team,status) VALUES(?,?,?,?,?)",(mid,1,batting_first,bowling_first,'active'))
        iid=cur2.lastrowid
        bat_p=t1p if batting_first==team1 else t2p
        for i,p in enumerate(bat_p):
            conn.execute("INSERT INTO cricket_batting(inning_id,player_name,batting_order) VALUES(?,?,?)",(iid,p,i+1))
        conn.execute("DELETE FROM cricket_players WHERE match_id=?",(mid,))
        conn.execute("INSERT INTO cricket_players VALUES(?,?,?)",(mid,team1,json.dumps(t1p)))
        conn.execute("INSERT INTO cricket_players VALUES(?,?,?)",(mid,team2,json.dumps(t2p)))
        # Save structured player data (with roles + subs) to cricket_event_players
        if event_id and (t1_structured or t2_structured):
            conn.execute("DELETE FROM cricket_event_players WHERE event_id=?",(event_id,))
            for team_key, structured in [('team1', t1_structured), ('team2', t2_structured)]:
                if not structured: continue
                for i, p in enumerate(structured.get('main') or []):
                    pname = (p.get('player_name') or '').strip()
                    if pname:
                        conn.execute("INSERT INTO cricket_event_players(event_id,team,player_name,role,is_impact,player_order) VALUES(?,?,?,?,?,?)",
                            (event_id, team_key, pname, p.get('role','batsman'), 0, i+1))
                for i, p in enumerate(structured.get('subs') or []):
                    pname = (p.get('player_name') or '').strip()
                    if pname:
                        conn.execute("INSERT INTO cricket_event_players(event_id,team,player_name,role,is_impact,player_order) VALUES(?,?,?,?,?,?)",
                            (event_id, team_key, pname, p.get('role','batsman'), 1, i+1))
                # Also sync to event_players table
                conn.execute("DELETE FROM event_players WHERE event_id=? AND team=?",(event_id, team_key))
                for i, p in enumerate(structured.get('main') or []):
                    pname = (p.get('player_name') or '').strip()
                    if pname:
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,?,?)",
                            (event_id, team_key, pname, p.get('role','batsman'), 0, i+1))
                for i, p in enumerate(structured.get('subs') or []):
                    pname = (p.get('player_name') or '').strip()
                    if pname:
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,?,?)",
                            (event_id, team_key, pname, p.get('role','batsman'), 1, i+1))
        conn.commit()
    return jsonify({'success':True,'match_id':mid,'inning_id':iid})


@app.route('/api/match/<int:mid>', methods=['GET'])
def api_get_match(mid):
    state=get_cricket_match_state(mid)
    return jsonify(state) if state else (jsonify({'error':'not found'}),404)


@app.route('/api/match/<int:mid>/players')
def api_players(mid):
    with get_db() as conn:
        rows=conn.execute("SELECT team,players FROM cricket_players WHERE match_id=?",(mid,)).fetchall()
    return jsonify({r['team']:json.loads(r['players']) for r in rows})


@app.route('/api/match/<int:mid>/set_batsmen', methods=['POST'])
def api_set_batsmen(mid):
    d=request.json; iid=d['inning_id']
    with get_db() as conn:
        conn.execute("UPDATE cricket_batting SET is_on_strike=0 WHERE inning_id=?",(iid,))
        conn.execute("UPDATE cricket_batting SET is_on_strike=1 WHERE inning_id=? AND player_name=?",(iid,d['striker']))
        conn.execute("UPDATE cricket_batting SET is_on_strike=2 WHERE inning_id=? AND player_name=?",(iid,d['non_striker']))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/match/<int:mid>/set_bowler', methods=['POST'])
def api_set_bowler(mid):
    d=request.json; iid,bname=d['inning_id'],d['bowler']
    with get_db() as conn:
        if not conn.execute("SELECT id FROM cricket_bowling WHERE inning_id=? AND player_name=?",(iid,bname)).fetchone():
            conn.execute("INSERT INTO cricket_bowling(inning_id,player_name) VALUES(?,?)",(iid,bname))
        # Always update current_bowler_name so the delivery endpoint knows who is bowling
        conn.execute("UPDATE cricket_innings SET current_bowler_name=? WHERE id=?",(bname,iid))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/match/<int:mid>/delivery', methods=['POST'])
def api_delivery(mid):
    d=request.json; iid=d['inning_id']
    runs=int(d.get('runs',0)); ext_type=d.get('extra_type',''); ext_runs=int(d.get('extra_runs',0))
    is_wkt=bool(d.get('is_wicket',False)); wkt_type=d.get('wicket_type',''); fielder=d.get('fielder','').strip()
    with get_db() as conn:
        inn=dict(conn.execute("SELECT * FROM cricket_innings WHERE id=?",(iid,)).fetchone())
        match=dict(conn.execute("SELECT * FROM cricket_matches WHERE id=?",(mid,)).fetchone())
        striker=conn.execute("SELECT * FROM cricket_batting WHERE inning_id=? AND is_on_strike=1",(iid,)).fetchone()
        ns=conn.execute("SELECT * FROM cricket_batting WHERE inning_id=? AND is_on_strike=2",(iid,)).fetchone()
        bowler=conn.execute("SELECT * FROM cricket_bowling WHERE inning_id=? AND player_name=(SELECT current_bowler_name FROM cricket_innings WHERE id=?)",(iid,iid)).fetchone()
        if not striker or not bowler: return jsonify({'error':'Set batsmen/bowler first'}),400
        striker,bowler_rec=dict(striker),dict(bowler); ns=dict(ns) if ns else None
        # Save undo snapshot BEFORE modifying anything
        innings_snap = json.dumps(dict(conn.execute("SELECT * FROM cricket_innings WHERE id=?",(iid,)).fetchone()))
        batting_snap  = json.dumps([dict(r) for r in conn.execute("SELECT * FROM cricket_batting WHERE inning_id=? ORDER BY id",(iid,)).fetchall()])
        bowling_snap  = json.dumps([dict(r) for r in conn.execute("SELECT * FROM cricket_bowling WHERE inning_id=? ORDER BY id",(iid,)).fetchall()])
        valid=ext_type not in ('wide','no_ball')
        # +1 penalty run for wide/no_ball already included in ext_runs by frontend; just use it
        total_scored=runs+ext_runs
        conn.execute("UPDATE cricket_innings SET total_runs=total_runs+? WHERE id=?",(total_scored,iid))
        if valid: conn.execute("UPDATE cricket_innings SET balls=balls+1 WHERE id=?",(iid,))
        if ext_type in ('wide','no_ball'): conn.execute("UPDATE cricket_innings SET extras=extras+1 WHERE id=?",(iid,))
        bat_runs=runs if ext_type not in ('wide','bye','leg_bye') else 0
        if valid:
            conn.execute("UPDATE cricket_batting SET runs=runs+?,balls=balls+1,fours=fours+?,sixes=sixes+? WHERE id=?",
                (bat_runs,1 if bat_runs==4 else 0,1 if bat_runs==6 else 0,striker['id']))
        else:
            conn.execute("UPDATE cricket_batting SET runs=runs+? WHERE id=?",(bat_runs,striker['id']))
        bowl_runs=total_scored if ext_type!='bye' else 0
        if valid:
            conn.execute("UPDATE cricket_bowling SET runs=runs+?,balls=balls+1,current_over_balls=current_over_balls+1,current_over_runs=current_over_runs+? WHERE id=?",(bowl_runs,bowl_runs,bowler_rec['id']))
        else:
            conn.execute("UPDATE cricket_bowling SET runs=runs+?,current_over_runs=current_over_runs+? WHERE id=?",(bowl_runs,bowl_runs,bowler_rec['id']))
        ub=dict(conn.execute("SELECT * FROM cricket_bowling WHERE id=?",(bowler_rec['id'],)).fetchone())
        if ub['current_over_balls']==6:
            if ub['current_over_runs']==0: conn.execute("UPDATE cricket_bowling SET maidens=maidens+1 WHERE id=?",(bowler_rec['id'],))
            conn.execute("UPDATE cricket_bowling SET current_over_balls=0,current_over_runs=0 WHERE id=?",(bowler_rec['id'],))
        if is_wkt:
            conn.execute("UPDATE cricket_innings SET wickets=wickets+1 WHERE id=?",(iid,))
            if wkt_type=='run_out':
                who=d.get('run_out_batsman','striker'); oid=striker['id'] if who=='striker' else (ns['id'] if ns else striker['id'])
                conn.execute("UPDATE cricket_batting SET is_out=1,out_type=?,fielder=?,is_on_strike=0 WHERE id=?",(wkt_type,fielder,oid))
            else:
                conn.execute("UPDATE cricket_batting SET is_out=1,out_type=?,bowler=?,fielder=?,is_on_strike=0 WHERE id=?",(wkt_type,bowler_rec['player_name'],fielder,striker['id']))
                conn.execute("UPDATE cricket_bowling SET wickets=wickets+1 WHERE id=?",(bowler_rec['id'],))
        if valid and not is_wkt and ns and runs%2==1:
            conn.execute("UPDATE cricket_batting SET is_on_strike=1 WHERE id=?",(ns['id'],))
            conn.execute("UPDATE cricket_batting SET is_on_strike=2 WHERE id=?",(striker['id'],))
        ui=dict(conn.execute("SELECT * FROM cricket_innings WHERE id=?",(iid,)).fetchone())
        if valid and ui['balls']%6==0 and ui['balls']>0:
            s=conn.execute("SELECT id FROM cricket_batting WHERE inning_id=? AND is_on_strike=1",(iid,)).fetchone()
            ns2=conn.execute("SELECT id FROM cricket_batting WHERE inning_id=? AND is_on_strike=2",(iid,)).fetchone()
            if s and ns2:
                conn.execute("UPDATE cricket_batting SET is_on_strike=2 WHERE id=?",(s['id'],))
                conn.execute("UPDATE cricket_batting SET is_on_strike=1 WHERE id=?",(ns2['id'],))
        shot_dir = d.get('shot_direction')  # float angle in degrees, or None
        conn.execute("INSERT INTO cricket_deliveries(inning_id,over_no,ball_no,batsman,bowler,runs,extra_type,extra_runs,is_wicket,wicket_type,fielder,shot_direction) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid,ui['balls']//6,ui['balls']%6,striker['player_name'],bowler_rec['player_name'],runs,ext_type,ext_runs,1 if is_wkt else 0,wkt_type,fielder,shot_dir))
        delivery_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO cricket_undo_log(inning_id,delivery_id,innings_snapshot,batting_snapshot,bowling_snapshot) VALUES(?,?,?,?,?)",
            (iid, delivery_id, innings_snap, batting_snap, bowling_snap))
        ui=dict(conn.execute("SELECT * FROM cricket_innings WHERE id=?",(iid,)).fetchone())
        over=ui['balls']>=match['total_overs']*6 or ui['wickets']>=10; result=None
        if inn['inning_no']==2 and not over:
            fi=conn.execute("SELECT * FROM cricket_innings WHERE match_id=? AND inning_no=1",(mid,)).fetchone()
            if fi and ui['total_runs']>=fi['total_runs']+1:
                over=True; wl=10-ui['wickets']
                result=f"{ui['batting_team']} won by {wl} wicket{'s' if wl!=1 else ''}!"
        if over:
            conn.execute("UPDATE cricket_innings SET status='completed', current_bowler_name=NULL WHERE id=?",(iid,))
            if inn['inning_no']==1:
                cur=conn.execute("INSERT INTO cricket_innings(match_id,inning_no,batting_team,bowling_team,status) VALUES(?,?,?,?,?)",(mid,2,ui['bowling_team'],ui['batting_team'],'active'))
                niid=cur.lastrowid
                pm={r['team']:json.loads(r['players']) for r in conn.execute("SELECT team,players FROM cricket_players WHERE match_id=?",(mid,)).fetchall()}
                for i,p in enumerate(pm.get(ui['bowling_team'],[])):
                    conn.execute("INSERT INTO cricket_batting(inning_id,player_name,batting_order) VALUES(?,?,?)",(niid,p,i+1))
                conn.commit()
                return jsonify({'success':True,'innings_over':True,'new_inning':True,'new_inning_id':niid,'delivery_id':delivery_id})
            else:
                fi=dict(conn.execute("SELECT * FROM cricket_innings WHERE match_id=? AND inning_no=1",(mid,)).fetchone())
                if not result:
                    if ui['total_runs']>fi['total_runs']: wl=10-ui['wickets']; result=f"{ui['batting_team']} won by {wl} wicket{'s' if wl!=1 else ''}!"
                    elif fi['total_runs']>ui['total_runs']: diff=fi['total_runs']-ui['total_runs']; result=f"{fi['batting_team']} won by {diff} run{'s' if diff!=1 else ''}!"
                    else: result="Match Tied!"
                conn.execute("UPDATE cricket_matches SET status='completed',result=? WHERE id=?",(result,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=(SELECT event_id FROM cricket_matches WHERE id=?)",(result,mid))
                conn.commit()
                return jsonify({'success':True,'innings_over':True,'match_over':True,'result':result,'delivery_id':delivery_id})
        conn.commit()
    return jsonify({'success':True,'innings_over':False,'delivery_id':delivery_id})


@app.route('/api/match/<int:mid>/undo_delivery', methods=['POST'])
def api_undo_delivery(mid):
    d=request.json; iid=d.get('inning_id')
    with get_db() as conn:
        log=conn.execute("SELECT * FROM cricket_undo_log WHERE inning_id=? ORDER BY id DESC LIMIT 1",(iid,)).fetchone()
        if not log: return jsonify({'error':'Nothing to undo'}),400
        log=dict(log)
        innings_data=json.loads(log['innings_snapshot'])
        batting_data=json.loads(log['batting_snapshot'])
        bowling_data=json.loads(log['bowling_snapshot'])
        conn.execute("UPDATE cricket_innings SET total_runs=?,wickets=?,balls=?,extras=?,status=?,current_bowler_name=? WHERE id=?",
            (innings_data['total_runs'],innings_data['wickets'],innings_data['balls'],innings_data['extras'],innings_data['status'],innings_data.get('current_bowler_name'),iid))
        for b in batting_data:
            conn.execute("UPDATE cricket_batting SET runs=?,balls=?,fours=?,sixes=?,is_out=?,out_type=?,bowler=?,fielder=?,is_on_strike=? WHERE id=?",
                (b['runs'],b['balls'],b['fours'],b['sixes'],b['is_out'],b['out_type'] or '',b['bowler'] or '',b['fielder'] or '',b['is_on_strike'],b['id']))
        for bw in bowling_data:
            conn.execute("UPDATE cricket_bowling SET balls=?,runs=?,wickets=?,maidens=?,current_over_balls=?,current_over_runs=? WHERE id=?",
                (bw['balls'],bw['runs'],bw['wickets'],bw['maidens'],bw['current_over_balls'],bw['current_over_runs'],bw['id']))
        conn.execute("DELETE FROM cricket_deliveries WHERE id=?",(log['delivery_id'],))
        conn.execute("DELETE FROM cricket_undo_log WHERE id=?",(log['id'],))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/match/<int:mid>/new_batsman', methods=['POST'])
def api_new_batsman(mid):
    d = request.json
    iid = d['inning_id']
    player_name = d['player_name']
    end_of_over = bool(d.get('end_of_over', False))
    with get_db() as conn:
        if end_of_over:
            # Wicket fell on the last ball of an over.
            # The new batsman enters at the striker's end but the over has just
            # rotated, so: new batsman → non-striker (2), surviving non-striker → striker (1).
            surviving_ns = conn.execute(
                "SELECT id FROM cricket_batting WHERE inning_id=? AND is_on_strike=2", (iid,)
            ).fetchone()
            conn.execute(
                "UPDATE cricket_batting SET is_on_strike=2 WHERE inning_id=? AND player_name=?",
                (iid, player_name))
            if surviving_ns:
                conn.execute(
                    "UPDATE cricket_batting SET is_on_strike=1 WHERE id=?", (surviving_ns['id'],))
        else:
            # Mid-over wicket: new batsman takes the striker's position.
            conn.execute(
                "UPDATE cricket_batting SET is_on_strike=1 WHERE inning_id=? AND player_name=?",
                (iid, player_name))
        conn.commit()
    return jsonify({'success': True})


@app.route('/api/event/<int:eid>/live')
def api_event_live(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
    if not ev: return jsonify({'status':'not_found'})
    sport = get_sport_name(dict(ev))
    if 'cricket' in sport:
        with get_db() as conn:
            cm = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?",(eid,)).fetchone()
        if cm: return jsonify(get_cricket_match_state(cm['id']) or {'status':'no_match'})
    elif 'kabaddi' in sport: return jsonify(get_kabaddi_match_state(eid) or {'status':'no_match'})
    elif 'football' in sport: return jsonify(get_football_match_state(eid) or {'status':'no_match'})
    elif 'basketball' in sport: return jsonify(get_basketball_match_state(eid) or {'status':'no_match'})
    elif 'volleyball' in sport: return jsonify(get_volleyball_match_state(eid) or {'status':'no_match'})
    elif 'badminton' in sport: return jsonify(get_badminton_match_state(eid) or {'status':'no_match'})
    elif 'table tennis' in sport or 'tabletennis' in sport: return jsonify(get_tabletennis_match_state(eid) or {'status':'no_match'})
    return jsonify({'status':'no_match'})


@app.route('/api/match/<int:mid>/overs')
def api_match_overs(mid):
    with get_db() as conn:
        match = conn.execute("SELECT total_overs FROM cricket_matches WHERE id=?", (mid,)).fetchone()
        if not match: return jsonify({'error': 'not found'}), 404
        total = match['total_overs']
        innings = conn.execute("SELECT id, inning_no, batting_team FROM cricket_innings WHERE match_id=? ORDER BY inning_no", (mid,)).fetchall()
        result = []
        for inn in innings:
            rows = conn.execute(
                "SELECT over_no, SUM(runs+extra_runs) as r FROM cricket_deliveries WHERE inning_id=? GROUP BY over_no ORDER BY over_no",
                (inn['id'],)).fetchall()
            over_map = {r['over_no']: r['r'] for r in rows}
            overs = [{'over': i+1, 'runs': over_map.get(i, 0)} for i in range(total)]
            result.append({'inning_no': inn['inning_no'], 'batting_team': inn['batting_team'], 'overs': overs})
    return jsonify({'innings': result, 'total_overs': total})


@app.route('/api/match/<int:mid>/deliveries')
def api_match_deliveries(mid):
    """Ball-by-ball deliveries for wagon wheel & analytics"""
    iid = request.args.get('inning_id', type=int)
    with get_db() as conn:
        if iid:
            rows = conn.execute(
                "SELECT * FROM cricket_deliveries WHERE inning_id=? ORDER BY over_no,ball_no", (iid,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT d.* FROM cricket_deliveries d "
                "JOIN cricket_innings i ON d.inning_id=i.id "
                "WHERE i.match_id=? ORDER BY i.inning_no,d.over_no,d.ball_no", (mid,)
            ).fetchall()
    return jsonify({'deliveries': [dict(r) for r in rows]})


@app.route('/api/delivery/<int:did>/direction', methods=['POST'])
def api_set_delivery_direction(did):
    """Set the shot direction angle (degrees) for a specific delivery."""
    data = request.get_json(force=True)
    angle = data.get('shot_direction')
    if angle is None:
        return jsonify({'error': 'shot_direction required'}), 400
    with get_db() as conn:
        conn.execute("UPDATE cricket_deliveries SET shot_direction=? WHERE id=?", (float(angle), did))
        conn.commit()
    return jsonify({'ok': True, 'delivery_id': did, 'shot_direction': angle})


@app.route('/api/match/<int:mid>/ww_settings', methods=['POST'])
@admin_required
def api_ww_settings(mid):
    """Toggle wagon wheel direction feature on/off for a match."""
    data = request.get_json(force=True)
    enabled = 1 if data.get('enabled', True) else 0
    with get_db() as conn:
        conn.execute("UPDATE cricket_matches SET ww_direction_enabled=? WHERE id=?", (enabled, mid))
        conn.commit()
    return jsonify({'ok': True, 'ww_direction_enabled': enabled})


@app.route('/api/match/<int:mid>/ww_settings', methods=['GET'])
def api_get_ww_settings(mid):
    """Get wagon wheel direction feature setting."""
    with get_db() as conn:
        row = conn.execute("SELECT ww_direction_enabled FROM cricket_matches WHERE id=?", (mid,)).fetchone()
    if not row:
        return jsonify({'error': 'match not found'}), 404
    return jsonify({'ww_direction_enabled': row['ww_direction_enabled'] if row['ww_direction_enabled'] is not None else 1})


@app.route('/api/match/<int:mid>/nrr')
def api_match_nrr(mid):
    """Net Run Rate calculation for the match"""
    with get_db() as conn:
        match = conn.execute("SELECT * FROM cricket_matches WHERE id=?", (mid,)).fetchone()
        if not match: return jsonify({'error':'not found'}), 404
        match = dict(match)
        innings = [dict(i) for i in conn.execute(
            "SELECT * FROM cricket_innings WHERE match_id=? ORDER BY inning_no", (mid,)
        ).fetchall()]
    result = {'match_id': mid, 'total_overs': match['total_overs'], 'innings': []}
    for inn in innings:
        overs_faced = inn['balls'] / 6 if inn['balls'] > 0 else 0
        rpo = round(inn['total_runs'] / overs_faced, 3) if overs_faced > 0 else 0
        result['innings'].append({
            'inning_no': inn['inning_no'],
            'batting_team': inn['batting_team'],
            'runs': inn['total_runs'],
            'balls': inn['balls'],
            'overs_faced': round(overs_faced, 2),
            'rpo': rpo,
            'wickets': inn['wickets']
        })
    if len(innings) == 2:
        i1, i2 = result['innings'][0], result['innings'][1]
        rpo1 = i1['rpo']; rpo2 = i2['rpo']
        result['nrr'] = {
            i1['batting_team']: round(rpo1 - rpo2, 3),
            i2['batting_team']: round(rpo2 - rpo1, 3)
        }
    return jsonify(result)


@app.route('/api/event/<int:eid>/players')
def api_event_players(eid):
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order", (eid,)).fetchall()]
    by_team = {'team1': {'main': [], 'subs': []}, 'team2': {'main': [], 'subs': []}}
    for p in rows:
        tk = p['team']
        if tk in by_team:
            key = 'subs' if p['is_sub'] else 'main'
            by_team[tk][key].append(p)
    return jsonify(by_team)




@app.route('/api/badminton/<int:eid>/substitute', methods=['POST'])
@admin_required
def api_badminton_substitute(eid):
    d = request.json; state = get_badminton_match_state(eid)
    if not state: return jsonify({'error':'no match'}),404
    out_p = d.get('out_player','').strip(); in_p = d.get('in_player','').strip(); team = d.get('team','').strip()
    if not out_p or not in_p or not team: return jsonify({'error':'Missing fields'}),400
    with get_db() as conn:
        if not conn.execute("SELECT id FROM badminton_players WHERE match_id=? AND team=? AND player_name=?",(state['id'],team,out_p)).fetchone():
            return jsonify({'error':f'{out_p} not found'}),404
        conn.execute("UPDATE badminton_players SET player_name=?,smashes=0,net_kills=0,drops=0 WHERE match_id=? AND team=? AND player_name=?",(in_p,state['id'],team,out_p))
        conn.commit()
    return jsonify({'success':True,'state':get_badminton_match_state(eid)})


# ── TABLE TENNIS ADMIN & API ──────────────────────────────────

@app.route('/admin/tabletennis/<int:eid>')
@admin_required
def admin_tabletennis_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?",(eid,)).fetchone()
        if not ev: flash('Not found','error'); return redirect(url_for('admin_events'))
        ep = conn.execute("SELECT * FROM event_players WHERE event_id=? ORDER BY team,is_sub,player_order",(eid,)).fetchall()
    t1_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team1' and not p['is_sub'])
    t2_players = '\n'.join(p['player_name'] for p in ep if p['team']=='team2' and not p['is_sub'])
    t1_subs = [p['player_name'] for p in ep if p['team']=='team1' and p['is_sub']]
    t2_subs = [p['player_name'] for p in ep if p['team']=='team2' and p['is_sub']]
    return render_template('admin/tabletennis_scoring.html', event=dict(ev), state=get_tabletennis_match_state(eid),
        preset_t1=t1_players, preset_t2=t2_players, t1_subs=t1_subs, t2_subs=t2_subs)


@app.route('/admin/tabletennis/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_tabletennis(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?",(eid,)).fetchone())
        if not conn.execute("SELECT id FROM tabletennis_matches WHERE event_id=?",(eid,)).fetchone():
            match_type = request.form.get('match_type','best_of_5')
            player_mode = request.form.get('player_mode','singles')
            cur = conn.execute("INSERT INTO tabletennis_matches(event_id,team1,team2,match_type,player_mode,status) VALUES(?,?,?,?,?,?)",
                (eid,ev['team1'],ev['team2'],match_type,player_mode,'live'))
            mid = cur.lastrowid
            conn.execute("INSERT INTO tabletennis_games(match_id,game_no,status) VALUES(?,?,?)",(mid,1,'active'))
            for tk in ['team1','team2']:
                plist = [x.strip() for x in request.form.get(f'{tk}_players','').split('\n') if x.strip()]
                if not plist:
                    plist = [r['player_name'] for r in conn.execute("SELECT player_name FROM event_players WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",(eid,tk)).fetchall()]
                for p in plist:
                    conn.execute("INSERT INTO tabletennis_players(match_id,team,player_name) VALUES(?,?,?)",(mid,ev[tk],p))
                if plist:
                    conn.execute("DELETE FROM event_players WHERE event_id=? AND team=? AND is_sub=0",(eid,tk))
                    for i,p in enumerate(plist):
                        conn.execute("INSERT INTO event_players(event_id,team,player_name,role,is_sub,player_order) VALUES(?,?,?,?,0,?)",(eid,tk,p,'player',i+1))
            conn.execute("UPDATE events SET status='live' WHERE id=?",(eid,)); conn.commit()
    return redirect(url_for('admin_tabletennis_scoring',eid=eid))


@app.route('/api/tabletennis/<int:eid>/state')
def api_tabletennis_state(eid):
    state = get_tabletennis_match_state(eid)
    return jsonify(state or {'status':'no_match'})


@app.route('/api/tabletennis/<int:eid>/point', methods=['POST'])
@admin_required
def api_tabletennis_point(eid):
    d = request.json; state = get_tabletennis_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    mid = state['id']; team = d.get('team'); cg = state['current_game']
    shot_type = d.get('shot_type','rally'); server = d.get('server','')
    if not team: return jsonify({'error':'team required'}),400
    with get_db() as conn:
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        if team==state['team1']:
            conn.execute("UPDATE tabletennis_games SET t1_streak=t1_streak+1, t2_streak=0 WHERE id=?",(cg['id'],))
        else:
            conn.execute("UPDATE tabletennis_games SET t2_streak=t2_streak+1, t1_streak=0 WHERE id=?",(cg['id'],))
        conn.execute(f"UPDATE tabletennis_games SET {col}={col}+1, rally_count=rally_count+1, server=? WHERE id=?",(team,cg['id']))
        ug = dict(conn.execute("SELECT * FROM tabletennis_games WHERE id=?",(cg['id'],)).fetchone())
        t1s,t2s = ug['team1_score'],ug['team2_score']
        try:
            conn.execute("UPDATE tabletennis_players SET points_won=points_won+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
        except Exception: pass
        if shot_type in ('smash','loop','drop','unforced_error'):
            stat_col = {'smash':'smashes','loop':'loops','drop':'drops','unforced_error':'unforced_errors'}.get(shot_type)
            if stat_col:
                try: conn.execute(f"UPDATE tabletennis_players SET {stat_col}={stat_col}+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
                except Exception: pass
        conn.execute("INSERT INTO tabletennis_points(match_id,game_id,team,shot_type,t1_score_after,t2_score_after,server) VALUES(?,?,?,?,?,?,?)",
                     (mid,cg['id'],team,shot_type,t1s,t2s,team))
        # Table tennis: first to 11, win by 2, max 20 (deuce at 10-10)
        gw = None
        if t1s >= 11 and t1s - t2s >= 2: gw = state['team1']
        elif t2s >= 11 and t2s - t1s >= 2: gw = state['team2']
        elif t1s >= 20 and t1s > t2s: gw = state['team1']
        elif t2s >= 20 and t2s > t1s: gw = state['team2']
        go=False; mo=False; mr=None
        if gw:
            go=True
            conn.execute("UPDATE tabletennis_games SET winner=?,status='completed' WHERE id=?",(gw,cg['id']))
            gc='team1_games' if gw==state['team1'] else 'team2_games'
            conn.execute(f"UPDATE tabletennis_matches SET {gc}={gc}+1 WHERE id=?",(mid,))
            um = dict(conn.execute("SELECT * FROM tabletennis_matches WHERE id=?",(mid,)).fetchone())
            wn = 1 if um['match_type']=='single' else (2 if um['match_type']=='best_of_3' else (3 if um['match_type']=='best_of_5' else 4))
            if um['team1_games']>=wn: mo=True; mr=f"{state['team1']} won {um['team1_games']}-{um['team2_games']}!"
            elif um['team2_games']>=wn: mo=True; mr=f"{state['team2']} won {um['team2_games']}-{um['team1_games']}!"
            else:
                ng=cg['game_no']+1
                conn.execute("INSERT INTO tabletennis_games(match_id,game_no,status,server) VALUES(?,?,?,?)",(mid,ng,'active',gw))
                conn.execute("UPDATE tabletennis_matches SET current_game=? WHERE id=?",(ng,mid))
            if mo:
                conn.execute("UPDATE tabletennis_matches SET status='completed',result=? WHERE id=?",(mr,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(mr,eid))
        conn.commit()
    return jsonify({'success':True,'game_over':go,'game_winner':gw,'match_over':mo,'match_result':mr,'state':get_tabletennis_match_state(eid)})


@app.route('/api/tabletennis/<int:eid>/undo', methods=['POST'])
@admin_required
def api_tabletennis_undo(eid):
    state = get_tabletennis_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    mid = state['id']; cg = state['current_game']
    with get_db() as conn:
        last = conn.execute("SELECT * FROM tabletennis_points WHERE match_id=? AND game_id=? ORDER BY id DESC LIMIT 1",(mid,cg['id'])).fetchone()
        if not last: return jsonify({'error':'Nothing to undo'}),400
        last = dict(last); team = last['team']
        col = 'team1_score' if team==state['team1'] else 'team2_score'
        conn.execute(f"UPDATE tabletennis_games SET {col}=MAX(0,{col}-1), rally_count=MAX(0,rally_count-1) WHERE id=?",(cg['id'],))
        conn.execute("DELETE FROM tabletennis_points WHERE id=?",(last['id'],))
        shot_type = last.get('shot_type','rally')
        if shot_type in ('smash','loop','drop','unforced_error'):
            stat_col = {'smash':'smashes','loop':'loops','drop':'drops','unforced_error':'unforced_errors'}.get(shot_type)
            if stat_col:
                try: conn.execute(f"UPDATE tabletennis_players SET {stat_col}=MAX(0,{stat_col}-1) WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,team))
                except Exception: pass
        conn.commit()
    return jsonify({'success':True,'state':get_tabletennis_match_state(eid)})


@app.route('/api/tabletennis/<int:eid>/fault', methods=['POST'])
@admin_required
def api_tabletennis_fault(eid):
    d = request.json; state = get_tabletennis_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    faulting_team = d.get('team'); fault_type = d.get('fault_type','service_fault')
    if not faulting_team: return jsonify({'error':'team required'}),400
    other_team = state['team2'] if faulting_team==state['team1'] else state['team1']
    mid = state['id']; cg = state['current_game']
    with get_db() as conn:
        if fault_type == 'service_fault':
            try: conn.execute("UPDATE tabletennis_players SET service_faults=service_faults+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,faulting_team))
            except Exception: pass
        col = 'team1_score' if other_team==state['team1'] else 'team2_score'
        if faulting_team==state['team1']:
            conn.execute("UPDATE tabletennis_games SET t2_streak=t2_streak+1, t1_streak=0 WHERE id=?",(cg['id'],))
        else:
            conn.execute("UPDATE tabletennis_games SET t1_streak=t1_streak+1, t2_streak=0 WHERE id=?",(cg['id'],))
        conn.execute(f"UPDATE tabletennis_games SET {col}={col}+1, rally_count=rally_count+1, server=? WHERE id=?",(other_team,cg['id']))
        ug = dict(conn.execute("SELECT * FROM tabletennis_games WHERE id=?",(cg['id'],)).fetchone())
        t1s,t2s = ug['team1_score'],ug['team2_score']
        try: conn.execute("UPDATE tabletennis_players SET points_won=points_won+1 WHERE match_id=? AND team=? ORDER BY id LIMIT 1",(mid,other_team))
        except Exception: pass
        conn.execute("INSERT INTO tabletennis_points(match_id,game_id,team,shot_type,fault_type,t1_score_after,t2_score_after,server) VALUES(?,?,?,?,?,?,?,?)",
                     (mid,cg['id'],other_team,'fault',fault_type,t1s,t2s,other_team))
        gw = None
        if t1s>=11 and t1s-t2s>=2: gw=state['team1']
        elif t2s>=11 and t2s-t1s>=2: gw=state['team2']
        elif t1s>=20 and t1s>t2s: gw=state['team1']
        elif t2s>=20 and t2s>t1s: gw=state['team2']
        go=False; mo=False; mr=None
        if gw:
            go=True
            conn.execute("UPDATE tabletennis_games SET winner=?,status='completed' WHERE id=?",(gw,cg['id']))
            gc='team1_games' if gw==state['team1'] else 'team2_games'
            conn.execute(f"UPDATE tabletennis_matches SET {gc}={gc}+1 WHERE id=?",(mid,))
            um = dict(conn.execute("SELECT * FROM tabletennis_matches WHERE id=?",(mid,)).fetchone())
            wn = 1 if um['match_type']=='single' else (2 if um['match_type']=='best_of_3' else (3 if um['match_type']=='best_of_5' else 4))
            if um['team1_games']>=wn: mo=True; mr=f"{state['team1']} won {um['team1_games']}-{um['team2_games']}!"
            elif um['team2_games']>=wn: mo=True; mr=f"{state['team2']} won {um['team2_games']}-{um['team1_games']}!"
            else:
                ng=cg['game_no']+1
                conn.execute("INSERT INTO tabletennis_games(match_id,game_no,status,server) VALUES(?,?,?,?)",(mid,ng,'active',gw))
                conn.execute("UPDATE tabletennis_matches SET current_game=? WHERE id=?",(ng,mid))
            if mo:
                conn.execute("UPDATE tabletennis_matches SET status='completed',result=? WHERE id=?",(mr,mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?",(mr,eid))
        conn.commit()
    return jsonify({'success':True,'game_over':go,'game_winner':gw,'match_over':mo,'match_result':mr,'state':get_tabletennis_match_state(eid)})


@app.route('/api/tabletennis/<int:eid>/card', methods=['POST'])
@admin_required
def api_tabletennis_card(eid):
    d = request.json; state = get_tabletennis_match_state(eid)
    if not state: return jsonify({'error':'no match'}),400
    mid = state['id']; cg = state.get('current_game')
    with get_db() as conn:
        conn.execute("INSERT INTO tabletennis_cards(match_id,game_id,team,player,card_type,reason) VALUES(?,?,?,?,?,?)",
                     (mid, cg['id'] if cg else None, d.get('team'), d.get('player'), d.get('card_type','yellow'), d.get('reason','')))
        conn.commit()
    return jsonify({'success':True})


@app.route('/api/tabletennis/<int:eid>/server', methods=['POST'])
@admin_required
def api_tabletennis_set_server(eid):
    d = request.json; state = get_tabletennis_match_state(eid)
    if not state or not state['current_game']: return jsonify({'error':'no active game'}),400
    server = d.get('server',''); cg = state['current_game']
    with get_db() as conn:
        conn.execute("UPDATE tabletennis_games SET server=? WHERE id=?",(server,cg['id']))
        conn.commit()
    return jsonify({'success':True,'server':server,'state':get_tabletennis_match_state(eid)})


@app.route('/api/tabletennis/<int:eid>/stats')
@admin_required
def api_tabletennis_stats(eid):
    state = get_tabletennis_match_state(eid)
    if not state: return jsonify({'error':'no match'}),400
    mid = state['id']
    with get_db() as conn:
        points = [dict(p) for p in conn.execute("SELECT * FROM tabletennis_points WHERE match_id=? ORDER BY id",(mid,)).fetchall()]
        cards = [dict(c) for c in conn.execute("SELECT * FROM tabletennis_cards WHERE match_id=? ORDER BY id",(mid,)).fetchall()]
        players = [dict(p) for p in conn.execute("SELECT * FROM tabletennis_players WHERE match_id=? ORDER BY team,player_name",(mid,)).fetchall()]
    return jsonify({'success':True,'points':points,'cards':cards,'players':players,'state':state})



def get_chess_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM chess_matches WHERE event_id=?", (event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['games'] = [dict(g) for g in conn.execute(
            "SELECT * FROM chess_games WHERE match_id=? ORDER BY game_no", (m['id'],)).fetchall()]
        m['current_game_obj'] = next((g for g in m['games'] if g['result'] == 'pending'), None)
        return m


@app.route('/admin/chess/<int:eid>')
@admin_required
def admin_chess_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?", (eid,)).fetchone()
        if not ev: flash('Not found', 'error'); return redirect(url_for('admin_events'))
    state = get_chess_match_state(eid)
    return render_template('admin/chess_scoring.html', event=dict(ev), state=state)


@app.route('/admin/chess/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_chess(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
        if not conn.execute("SELECT id FROM chess_matches WHERE event_id=?", (eid,)).fetchone():
            total_games = int(request.form.get('total_games', 3) or 3)
            time_ctrl = request.form.get('time_control', 'none')
            game_mode = request.form.get('game_mode', 'pvp')
            ai_difficulty = request.form.get('ai_difficulty', 'medium')
            cur = conn.execute(
                "INSERT INTO chess_matches(event_id,player1,player2,total_games,time_control,status,game_mode,ai_difficulty) VALUES(?,?,?,?,?,?,?,?)",
                (eid, ev['team1'], ev['team2'], total_games, time_ctrl, 'live', game_mode, ai_difficulty))
            mid = cur.lastrowid
            # First game: player1 plays white
            conn.execute("INSERT INTO chess_games(match_id,game_no,white_player,black_player,result) VALUES(?,?,?,?,?)",
                (mid, 1, ev['team1'], ev['team2'], 'pending'))
            conn.execute("UPDATE events SET status='live' WHERE id=?", (eid,))
            conn.commit()
    return redirect(url_for('admin_chess_scoring', eid=eid))


@app.route('/api/chess/<int:eid>/state')
def api_chess_state(eid):
    state = get_chess_match_state(eid)
    if not state: return jsonify({'error': 'not found'}), 404
    return jsonify(state)


@app.route('/api/chess/<int:eid>/result', methods=['POST'])
@admin_required
def api_chess_game_result(eid):
    d = request.json
    state = get_chess_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    mid = state['id']
    game_id = d.get('game_id')
    result = d.get('result')  # 'white', 'black', 'draw'
    moves = int(d.get('moves', 0) or 0)
    opening = d.get('opening', '')
    duration = int(d.get('duration_minutes', 0) or 0)
    notes = d.get('notes', '')

    if result not in ('white', 'black', 'draw'):
        return jsonify({'error': 'Invalid result'}), 400

    with get_db() as conn:
        game = conn.execute("SELECT * FROM chess_games WHERE id=? AND match_id=?", (game_id, mid)).fetchone()
        if not game: return jsonify({'error': 'Game not found'}), 404
        game = dict(game)

        winner = None
        p1_pts = 0.0; p2_pts = 0.0
        if result == 'white':
            winner = game['white_player']
            if winner == state['player1']: p1_pts = 1.0
            else: p2_pts = 1.0
        elif result == 'black':
            winner = game['black_player']
            if winner == state['player1']: p1_pts = 1.0
            else: p2_pts = 1.0
        else:  # draw
            p1_pts = 0.5; p2_pts = 0.5

        conn.execute(
            "UPDATE chess_games SET result=?,winner=?,moves=?,opening=?,duration_minutes=?,notes=? WHERE id=?",
            (result, winner, moves, opening, duration, notes, game_id))
        conn.execute(
            "UPDATE chess_matches SET player1_score=player1_score+?,player2_score=player2_score+? WHERE id=?",
            (p1_pts, p2_pts, mid))

        # Check if match is over
        updated = dict(conn.execute("SELECT * FROM chess_matches WHERE id=?", (mid,)).fetchone())
        games_played = conn.execute("SELECT COUNT(*) FROM chess_games WHERE match_id=? AND result!='pending'", (mid,)).fetchone()[0]
        total = updated['total_games']
        games_left = total - games_played
        match_over = False; match_result = None

        # Win condition: majority of points
        p1s = updated['player1_score']; p2s = updated['player2_score']
        wins_needed = (total / 2) + 0.5
        if p1s >= wins_needed or p2s >= wins_needed or games_played >= total:
            match_over = True
            if p1s > p2s: match_result = f"{state['player1']} won {p1s:.1f}-{p2s:.1f}!"
            elif p2s > p1s: match_result = f"{state['player2']} won {p2s:.1f}-{p1s:.1f}!"
            else: match_result = f"Match Drawn {p1s:.1f}-{p2s:.1f}!"
            conn.execute("UPDATE chess_matches SET status='completed',result=? WHERE id=?", (match_result, mid))
            conn.execute("UPDATE events SET status='completed',result=? WHERE id=?", (match_result, eid))
        elif not match_over and games_played < total:
            # Start next game: alternate colors
            next_game_no = game['game_no'] + 1
            # Alternate: odd games p1=white, even games p2=white
            if next_game_no % 2 == 1:
                w, b = state['player1'], state['player2']
            else:
                w, b = state['player2'], state['player1']
            conn.execute("INSERT INTO chess_games(match_id,game_no,white_player,black_player,result) VALUES(?,?,?,?,?)",
                (mid, next_game_no, w, b, 'pending'))
            conn.execute("UPDATE chess_matches SET current_game=? WHERE id=?", (next_game_no, mid))

        conn.commit()

    return jsonify({'success': True, 'match_over': match_over, 'match_result': match_result,
                    'state': get_chess_match_state(eid)})


@app.route('/api/chess/<int:eid>/move', methods=['POST'])
@admin_required
def api_chess_record_move(eid):
    """Record a single chess board move (from the interactive board)."""
    d = request.json
    game_id = d.get('game_id')
    san = d.get('san', '')
    uci = d.get('uci', '')
    fen = d.get('fen', '')
    color = d.get('color', '')
    pgn = d.get('pgn', '')

    if not game_id or not san:
        return jsonify({'error': 'Missing data'}), 400

    with get_db() as conn:
        # Count existing moves for this game
        move_no = conn.execute("SELECT COUNT(*) FROM chess_moves WHERE game_id=?", (game_id,)).fetchone()[0] + 1
        conn.execute(
            "INSERT INTO chess_moves(game_id,move_no,san,uci,fen_after,color) VALUES(?,?,?,?,?,?)",
            (game_id, move_no, san, uci, fen, color))
        # Update current_fen and move count and pgn in chess_games
        conn.execute(
            "UPDATE chess_games SET current_fen=?, moves=?, pgn=? WHERE id=?",
            (fen, move_no, pgn, game_id))
        conn.commit()

    return jsonify({'success': True, 'move_no': move_no})


@app.route('/api/chess/<int:eid>/game/<int:gid>/moves')
def api_chess_game_moves(eid, gid):
    """Get all moves for a specific chess game (for board viewer)."""
    with get_db() as conn:
        # Verify game belongs to this event
        match = conn.execute("SELECT id FROM chess_matches WHERE event_id=?", (eid,)).fetchone()
        if not match:
            return jsonify({'error': 'Not found'}), 404
        game = conn.execute(
            "SELECT * FROM chess_games WHERE id=? AND match_id=?", (gid, match['id'])).fetchone()
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        game = dict(game)
        moves = [dict(m) for m in conn.execute(
            "SELECT * FROM chess_moves WHERE game_id=? ORDER BY move_no", (gid,)).fetchall()]
    return jsonify({'game': game, 'moves': moves})


@app.route('/api/chess/<int:eid>/live')
def api_chess_live(eid):
    """Get live state + current game moves for the board viewer."""
    state = get_chess_match_state(eid)
    if not state:
        return jsonify({'error': 'Not found'}), 404
    # Get moves for the current active game
    current_moves = []
    current_game_obj = state.get('current_game_obj')
    if current_game_obj:
        with get_db() as conn:
            current_moves = [dict(m) for m in conn.execute(
                "SELECT * FROM chess_moves WHERE game_id=? ORDER BY move_no",
                (current_game_obj['id'],)).fetchall()]
    state['current_moves'] = current_moves
    return jsonify(state)


# ── CARROM STATE & ADMIN ──────────────────────────────────

def get_carrom_match_state(event_id):
    with get_db() as conn:
        m = conn.execute("SELECT * FROM carrom_matches WHERE event_id=?", (event_id,)).fetchone()
        if not m: return None
        m = dict(m)
        m['boards'] = [dict(b) for b in conn.execute(
            "SELECT * FROM carrom_boards WHERE match_id=? ORDER BY board_no", (m['id'],)).fetchall()]
        m['current_board_obj'] = next((b for b in m['boards'] if b['status'] == 'active'), None)
        if m['current_board_obj']:
            cb_id = m['current_board_obj']['id']
            queen_event = conn.execute(
                "SELECT id FROM carrom_events WHERE match_id=? AND board_id=? AND event_type='queen' LIMIT 1",
                (m['id'], cb_id)).fetchone()
            m['current_board_obj']['queen_pocketed'] = bool(queen_event)
        m['events'] = [dict(e) for e in conn.execute(
            "SELECT * FROM carrom_events WHERE match_id=? ORDER BY id DESC LIMIT 30", (m['id'],)).fetchall()]
        return m


@app.route('/admin/carrom/<int:eid>')
@admin_required
def admin_carrom_scoring(eid):
    with get_db() as conn:
        ev = conn.execute("SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?", (eid,)).fetchone()
        if not ev: flash('Not found', 'error'); return redirect(url_for('admin_events'))
    state = get_carrom_match_state(eid)
    return render_template('admin/carrom_scoring.html', event=dict(ev), state=state)


@app.route('/admin/carrom/<int:eid>/start', methods=['POST'])
@admin_required
def admin_start_carrom(eid):
    with get_db() as conn:
        ev = dict(conn.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone())
        if not conn.execute("SELECT id FROM carrom_matches WHERE event_id=?", (eid,)).fetchone():
            total_boards = int(request.form.get('total_boards', 3) or 3)
            match_type = request.form.get('match_type', 'singles')
            t1_players = request.form.get('team1_players', '').strip()
            t2_players = request.form.get('team2_players', '').strip()
            # Try to insert with player cols; fall back if col missing
            try:
                cur = conn.execute(
                    "INSERT INTO carrom_matches(event_id,team1,team2,total_boards,match_type,status,team1_players,team2_players) VALUES(?,?,?,?,?,?,?,?)",
                    (eid, ev['team1'], ev['team2'], total_boards, match_type, 'live', t1_players, t2_players))
            except Exception:
                cur = conn.execute(
                    "INSERT INTO carrom_matches(event_id,team1,team2,total_boards,match_type,status) VALUES(?,?,?,?,?,?)",
                    (eid, ev['team1'], ev['team2'], total_boards, match_type, 'live'))
            mid = cur.lastrowid
            conn.execute("INSERT INTO carrom_boards(match_id,board_no,status) VALUES(?,?,?)", (mid, 1, 'active'))
            conn.execute("UPDATE events SET status='live' WHERE id=?", (eid,))
            conn.commit()
    return redirect(url_for('admin_carrom_scoring', eid=eid))


@app.route('/api/carrom/<int:eid>/state')
def api_carrom_state(eid):
    state = get_carrom_match_state(eid)
    if not state: return jsonify({'error': 'not found'}), 404
    return jsonify(state)


@app.route('/api/carrom/<int:eid>/score', methods=['POST'])
@admin_required
def api_carrom_score(eid):
    d = request.json
    state = get_carrom_match_state(eid)
    if not state or not state['current_board_obj']: return jsonify({'error': 'no active board'}), 400
    mid = state['id']; team = d.get('team'); pts = int(d.get('points', 1))
    event_type = d.get('event_type', 'piece')  # piece, queen, penalty, due
    note = d.get('note', '')
    cb = state['current_board_obj']

    # Guard: queen can only be pocketed once per board
    if event_type == 'queen' and cb.get('queen_pocketed'):
        return jsonify({'error': 'Queen already pocketed this board — only one queen per board!'}), 400

    with get_db() as conn:
        col = 'team1_score' if team == state['team1'] else 'team2_score'
        # Penalty: subtract from the penalized player's score
        if event_type == 'penalty':
            conn.execute(f"UPDATE carrom_boards SET {col}=MAX(0,{col}-?) WHERE id=?", (pts, cb['id']))
            conn.execute("INSERT INTO carrom_events(match_id,board_id,team,event_type,points,note) VALUES(?,?,?,?,?,?)",
                (mid, cb['id'], team, 'penalty', pts, note or f'Penalty -{pts} pts'))
        else:
            conn.execute(f"UPDATE carrom_boards SET {col}={col}+? WHERE id=?", (pts, cb['id']))
            conn.execute("INSERT INTO carrom_events(match_id,board_id,team,event_type,points,note) VALUES(?,?,?,?,?,?)",
                (mid, cb['id'], team, event_type, pts, note))

        ub = dict(conn.execute("SELECT * FROM carrom_boards WHERE id=?", (cb['id'],)).fetchone())
        t1s, t2s = ub['team1_score'], ub['team2_score']

        # Board win condition: reach 25 points first, OR admin manually ends board
        board_winner = None
        board_over = False
        if t1s >= 25: board_winner = state['team1']; board_over = True
        elif t2s >= 25: board_winner = state['team2']; board_over = True

        match_over = False; match_result = None
        if board_over:
            conn.execute("UPDATE carrom_boards SET winner=?,status='completed' WHERE id=?", (board_winner, cb['id']))
            bc = 'team1_boards' if board_winner == state['team1'] else 'team2_boards'
            conn.execute(f"UPDATE carrom_matches SET {bc}={bc}+1 WHERE id=?", (mid,))
            um = dict(conn.execute("SELECT * FROM carrom_matches WHERE id=?", (mid,)).fetchone())
            boards_to_win = (um['total_boards'] // 2) + 1
            if um['team1_boards'] >= boards_to_win:
                match_over = True; match_result = f"{state['team1']} won {um['team1_boards']}-{um['team2_boards']}!"
            elif um['team2_boards'] >= boards_to_win:
                match_over = True; match_result = f"{state['team2']} won {um['team2_boards']}-{um['team1_boards']}!"
            else:
                nb = um['current_board'] + 1
                conn.execute("INSERT INTO carrom_boards(match_id,board_no,status) VALUES(?,?,?)", (mid, nb, 'active'))
                conn.execute("UPDATE carrom_matches SET current_board=? WHERE id=?", (nb, mid))
            if match_over:
                conn.execute("UPDATE carrom_matches SET status='completed',result=? WHERE id=?", (match_result, mid))
                conn.execute("UPDATE events SET status='completed',result=? WHERE id=?", (match_result, eid))

        conn.commit()

    return jsonify({'success': True, 'board_over': board_over, 'board_winner': board_winner,
                    'match_over': match_over, 'match_result': match_result,
                    'state': get_carrom_match_state(eid)})


@app.route('/api/carrom/<int:eid>/end-board', methods=['POST'])
@admin_required
def api_carrom_end_board(eid):
    """Manually end a board and declare winner based on current scores."""
    state = get_carrom_match_state(eid)
    if not state or not state['current_board_obj']: return jsonify({'error': 'no active board'}), 400
    mid = state['id']; cb = state['current_board_obj']
    t1s = cb['team1_score']; t2s = cb['team2_score']
    if t1s == t2s: return jsonify({'error': 'Scores are tied, cannot end board'}), 400
    board_winner = state['team1'] if t1s > t2s else state['team2']
    with get_db() as conn:
        conn.execute("UPDATE carrom_boards SET winner=?,status='completed' WHERE id=?", (board_winner, cb['id']))
        bc = 'team1_boards' if board_winner == state['team1'] else 'team2_boards'
        conn.execute(f"UPDATE carrom_matches SET {bc}={bc}+1 WHERE id=?", (mid,))
        um = dict(conn.execute("SELECT * FROM carrom_matches WHERE id=?", (mid,)).fetchone())
        boards_to_win = (um['total_boards'] // 2) + 1
        match_over = False; match_result = None
        if um['team1_boards'] >= boards_to_win:
            match_over = True; match_result = f"{state['team1']} won {um['team1_boards']}-{um['team2_boards']}!"
        elif um['team2_boards'] >= boards_to_win:
            match_over = True; match_result = f"{state['team2']} won {um['team2_boards']}-{um['team1_boards']}!"
        else:
            nb = um['current_board'] + 1
            conn.execute("INSERT INTO carrom_boards(match_id,board_no,status) VALUES(?,?,?)", (mid, nb, 'active'))
            conn.execute("UPDATE carrom_matches SET current_board=? WHERE id=?", (nb, mid))
        if match_over:
            conn.execute("UPDATE carrom_matches SET status='completed',result=? WHERE id=?", (match_result, mid))
            conn.execute("UPDATE events SET status='completed',result=? WHERE id=?", (match_result, eid))
        conn.commit()
    return jsonify({'success': True, 'board_winner': board_winner, 'match_over': match_over,
                    'match_result': match_result, 'state': get_carrom_match_state(eid)})


@app.route('/api/carrom/<int:eid>/end-match', methods=['POST'])
@admin_required
def api_carrom_end_match(eid):
    state = get_carrom_match_state(eid)
    if not state: return jsonify({'error': 'no match'}), 404
    t1, t2 = state['team1_boards'], state['team2_boards']
    if t1 > t2: result = f"{state['team1']} won {t1}-{t2}!"
    elif t2 > t1: result = f"{state['team2']} won {t2}-{t1}!"
    else: result = "Match Tied!"
    with get_db() as conn:
        conn.execute("UPDATE carrom_matches SET status='completed',result=? WHERE id=?", (result, state['id']))
        conn.execute("UPDATE events SET status='completed',result=? WHERE id=?", (result, eid))
        conn.commit()
    return jsonify({'success': True, 'result': result})


@app.route('/api/carrom/<int:eid>/undo', methods=['POST'])
@admin_required
def api_carrom_undo(eid):
    """Undo the last scoring event on the current board."""
    state = get_carrom_match_state(eid)
    if not state or not state['current_board_obj']:
        return jsonify({'error': 'no active board'}), 400
    mid = state['id']; cb = state['current_board_obj']
    with get_db() as conn:
        last = conn.execute(
            "SELECT * FROM carrom_events WHERE match_id=? AND board_id=? ORDER BY id DESC LIMIT 1",
            (mid, cb['id'])).fetchone()
        if not last:
            return jsonify({'error': 'Nothing to undo'}), 400
        last = dict(last)
        col = 'team1_score' if last['team'] == state['team1'] else 'team2_score'
        if last['event_type'] == 'penalty':
            conn.execute(f"UPDATE carrom_boards SET {col}={col}+? WHERE id=?", (last['points'], cb['id']))
        else:
            conn.execute(f"UPDATE carrom_boards SET {col}=MAX(0,{col}-?) WHERE id=?", (last['points'], cb['id']))
        conn.execute("DELETE FROM carrom_events WHERE id=?", (last['id'],))
        conn.commit()
    return jsonify({'success': True, 'undone': last['event_type'], 'state': get_carrom_match_state(eid)})


# ── PUBLIC CARROM VIEWER (users only, no admin controls) ──

@app.route('/carrom/<int:eid>')
def public_carrom_view(eid):
    """Read-only live spectator view for carrom."""
    with get_db() as conn:
        ev = conn.execute(
            "SELECT e.*,s.name sport_name,s.icon sport_icon FROM events e LEFT JOIN sports s ON e.sport_id=s.id WHERE e.id=?", (eid,)
        ).fetchone()
    if not ev: return "Event not found", 404
    state = get_carrom_match_state(eid)
    return render_template('carrom_viewer.html', event=dict(ev), state=state, user=current_user())


# ── ADMIN SIGNUP ──────────────────────────────────────────

ADMIN_SIGNUP_KEY = os.environ.get('ATHENA_ADMIN_KEY', 'Ath3na@Gully#2026!')  # Set ATHENA_ADMIN_KEY env var to override

@app.route('/admin/signup', methods=['GET', 'POST'])
def admin_signup():
    if is_admin(): return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        signup_key = request.form.get('signup_key', '').strip()
        if signup_key != ADMIN_SIGNUP_KEY:
            flash('Invalid signup key. Contact the system administrator.', 'error')
            return render_template('admin/signup.html')

        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        full_name = request.form.get('full_name', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if not username or not email or not password:
            flash('Username, email and password are required.', 'error')
            return render_template('admin/signup.html')
        # Username constraints
        if len(username) < 3 or len(username) > 30:
            flash('Username must be 3–30 characters.', 'error')
            return render_template('admin/signup.html')
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            flash('Username may only contain letters, numbers and underscores.', 'error')
            return render_template('admin/signup.html')
        # Strong password requirements
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('admin/signup.html')
        missing = []
        if not re.search(r'[A-Z]', password): missing.append('an uppercase letter')
        if not re.search(r'[a-z]', password): missing.append('a lowercase letter')
        if not re.search(r'[0-9]', password): missing.append('a number')
        if not re.search(r'[^a-zA-Z0-9]', password): missing.append('a special character')
        if len(missing) > 1:
            flash(f'Password is too weak — must include {", ".join(missing)}.', 'error')
            return render_template('admin/signup.html')
        if username.lower() in password.lower():
            flash('Password must not contain your username.', 'error')
            return render_template('admin/signup.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('admin/signup.html')

        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users(username,email,password_hash,full_name,role) VALUES(?,?,?,?,?)",
                    (username, email, generate_password_hash(password), full_name, 'admin')
                )
                conn.commit()
            flash(f'Admin account "{username}" created successfully! You can now log in.', 'success')
            return redirect(url_for('admin_login'))
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash('Username or email already exists.', 'error')
            else:
                flash(f'Error creating account: {str(e)}', 'error')

    return render_template('admin/signup.html')


# ── SPORT POINTS TABLES ───────────────────────────────────

def compute_points_table(sport_id):
    """Build a standings/points table for the given sport_id."""
    with get_db() as conn:
        sport = conn.execute("SELECT * FROM sports WHERE id=?", (sport_id,)).fetchone()
        if not sport:
            return None, None
        sport = dict(sport)
        sport_name = sport['name'].lower()

        standings = {}  # key: team_name → dict of stats

        def ensure(team):
            if team not in standings:
                standings[team] = {'team': team, 'mp': 0, 'w': 0, 'd': 0, 'l': 0, 'pts': 0,
                                   'gf': 0, 'ga': 0}  # gf/ga = "for"/"against" (goals, runs, etc.)

        def record(t1, t2, s1, s2, pts_win=3, pts_draw=1, pts_loss=0):
            """Record a match result for two teams given their scores."""
            ensure(t1); ensure(t2)
            for t in (t1, t2): standings[t]['mp'] += 1
            standings[t1]['gf'] += s1; standings[t1]['ga'] += s2
            standings[t2]['gf'] += s2; standings[t2]['ga'] += s1
            if s1 > s2:
                standings[t1]['w'] += 1; standings[t1]['pts'] += pts_win
                standings[t2]['l'] += 1; standings[t2]['pts'] += pts_loss
            elif s2 > s1:
                standings[t2]['w'] += 1; standings[t2]['pts'] += pts_win
                standings[t1]['l'] += 1; standings[t1]['pts'] += pts_loss
            else:
                standings[t1]['d'] += 1; standings[t1]['pts'] += pts_draw
                standings[t2]['d'] += 1; standings[t2]['pts'] += pts_draw

        if 'cricket' in sport_name:
            rows = conn.execute(
                "SELECT cm.team1,cm.team2,cm.result,i1.total_runs r1,i1.wickets w1,"
                "i2.total_runs r2,i2.wickets w2 "
                "FROM cricket_matches cm "
                "JOIN events e ON cm.event_id=e.id "
                "LEFT JOIN cricket_innings i1 ON i1.match_id=cm.id AND i1.inning_no=1 "
                "LEFT JOIN cricket_innings i2 ON i2.match_id=cm.id AND i2.inning_no=2 "
                "WHERE e.sport_id=? AND cm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                r = dict(r)
                t1, t2 = r['team1'], r['team2']
                r1 = r['r1'] or 0; r2 = r['r2'] or 0
                ensure(t1); ensure(t2)
                for t in (t1, t2): standings[t]['mp'] += 1
                standings[t1]['gf'] += r1; standings[t1]['ga'] += r2
                standings[t2]['gf'] += r2; standings[t2]['ga'] += r1
                result = r.get('result') or ''
                if 'Tied' in result or 'tied' in result:
                    standings[t1]['d'] += 1; standings[t1]['pts'] += 1
                    standings[t2]['d'] += 1; standings[t2]['pts'] += 1
                elif t1 in result:
                    standings[t1]['w'] += 1; standings[t1]['pts'] += 3
                    standings[t2]['l'] += 1
                elif t2 in result:
                    standings[t2]['w'] += 1; standings[t2]['pts'] += 3
                    standings[t1]['l'] += 1
            label_gf = 'Runs For'; label_ga = 'Runs Against'

        elif 'football' in sport_name or 'soccer' in sport_name:
            rows = conn.execute(
                "SELECT fm.team1,fm.team2,fm.team1_score,fm.team2_score "
                "FROM football_matches fm JOIN events e ON fm.event_id=e.id "
                "WHERE e.sport_id=? AND fm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_score'], r['team2_score'])
            label_gf = 'GF'; label_ga = 'GA'

        elif 'kabaddi' in sport_name:
            rows = conn.execute(
                "SELECT km.team1,km.team2,km.team1_score,km.team2_score "
                "FROM kabaddi_matches km JOIN events e ON km.event_id=e.id "
                "WHERE e.sport_id=? AND km.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_score'], r['team2_score'])
            label_gf = 'Points For'; label_ga = 'Points Against'

        elif 'basketball' in sport_name:
            rows = conn.execute(
                "SELECT bm.team1,bm.team2,bm.team1_score,bm.team2_score "
                "FROM basketball_matches bm JOIN events e ON bm.event_id=e.id "
                "WHERE e.sport_id=? AND bm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_score'], r['team2_score'])
            label_gf = 'Pts For'; label_ga = 'Pts Against'

        elif 'volleyball' in sport_name:
            rows = conn.execute(
                "SELECT vm.team1,vm.team2,vm.team1_sets,vm.team2_sets "
                "FROM volleyball_matches vm JOIN events e ON vm.event_id=e.id "
                "WHERE e.sport_id=? AND vm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_sets'], r['team2_sets'], pts_draw=0)
            label_gf = 'Sets For'; label_ga = 'Sets Against'

        elif 'badminton' in sport_name:
            rows = conn.execute(
                "SELECT bm.team1,bm.team2,bm.team1_games,bm.team2_games "
                "FROM badminton_matches bm JOIN events e ON bm.event_id=e.id "
                "WHERE e.sport_id=? AND bm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_games'], r['team2_games'], pts_draw=0)
            label_gf = 'Games For'; label_ga = 'Games Against'

        elif 'table tennis' in sport_name or 'tabletennis' in sport_name:
            rows = conn.execute(
                "SELECT tm.team1,tm.team2,tm.team1_games,tm.team2_games "
                "FROM tabletennis_matches tm JOIN events e ON tm.event_id=e.id "
                "WHERE e.sport_id=? AND tm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_games'], r['team2_games'], pts_draw=0)
            label_gf = 'Games For'; label_ga = 'Games Against'

        elif 'chess' in sport_name:
            rows = conn.execute(
                "SELECT cm.player1,cm.player2,cm.player1_score,cm.player2_score "
                "FROM chess_matches cm JOIN events e ON cm.event_id=e.id "
                "WHERE e.sport_id=? AND cm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                t1, t2 = r['player1'], r['player2']
                s1, s2 = (r['player1_score'] or 0), (r['player2_score'] or 0)
                ensure(t1); ensure(t2)
                for t in (t1, t2): standings[t]['mp'] += 1
                standings[t1]['gf'] += s1; standings[t1]['ga'] += s2
                standings[t2]['gf'] += s2; standings[t2]['ga'] += s1
                if s1 > s2:
                    standings[t1]['w'] += 1; standings[t1]['pts'] += 3
                    standings[t2]['l'] += 1
                elif s2 > s1:
                    standings[t2]['w'] += 1; standings[t2]['pts'] += 3
                    standings[t1]['l'] += 1
                else:
                    standings[t1]['d'] += 1; standings[t1]['pts'] += 1
                    standings[t2]['d'] += 1; standings[t2]['pts'] += 1
            label_gf = 'Game Pts For'; label_ga = 'Game Pts Against'

        elif 'carrom' in sport_name:
            rows = conn.execute(
                "SELECT cm.team1,cm.team2,cm.team1_boards,cm.team2_boards "
                "FROM carrom_matches cm JOIN events e ON cm.event_id=e.id "
                "WHERE e.sport_id=? AND cm.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                record(r['team1'], r['team2'], r['team1_boards'], r['team2_boards'], pts_draw=1)
            label_gf = 'Boards For'; label_ga = 'Boards Against'
        else:
            # Generic fallback: just use events result field
            rows = conn.execute(
                "SELECT e.team1,e.team2,e.result "
                "FROM events e WHERE e.sport_id=? AND e.status='completed'", (sport_id,)).fetchall()
            for r in rows:
                t1, t2 = r['team1'], r['team2']
                ensure(t1); ensure(t2)
                for t in (t1, t2): standings[t]['mp'] += 1
                result = r.get('result') or ''
                if t1 in result and 'won' in result:
                    standings[t1]['w'] += 1; standings[t1]['pts'] += 3; standings[t2]['l'] += 1
                elif t2 in result and 'won' in result:
                    standings[t2]['w'] += 1; standings[t2]['pts'] += 3; standings[t1]['l'] += 1
                else:
                    standings[t1]['d'] += 1; standings[t1]['pts'] += 1
                    standings[t2]['d'] += 1; standings[t2]['pts'] += 1
            label_gf = 'For'; label_ga = 'Against'

        # Add goal-difference
        for v in standings.values():
            v['gd'] = v['gf'] - v['ga']

        # Sort: pts desc, then gd desc, then gf desc, then name asc
        table = sorted(standings.values(),
                       key=lambda x: (-x['pts'], -x['gd'], -x['gf'], x['team']))
        return sport, table, label_gf, label_ga


@app.route('/points')
def points_overview():
    """Overview of all sports with their points tables."""
    with get_db() as conn:
        sports = [dict(s) for s in conn.execute(
            "SELECT s.*, COUNT(DISTINCT CASE WHEN e.status='completed' THEN e.id END) completed_events "
            "FROM sports s LEFT JOIN events e ON e.sport_id=s.id "
            "WHERE s.is_active=1 GROUP BY s.id ORDER BY s.name").fetchall()]
    # Build a mini-table (top 3) for each sport for preview
    previews = {}
    for s in sports:
        result = compute_points_table(s['id'])
        if result and result[0]:
            previews[s['id']] = result[1]  # table (index 1; result = sport, table, label_gf, label_ga)
        else:
            previews[s['id']] = []
    return render_template('points_overview.html', sports=sports, previews=previews, user=current_user())


@app.route('/points/<int:sport_id>')
def sport_points(sport_id):
    """Full points table for a single sport."""
    result = compute_points_table(sport_id)
    if not result or not result[0]:
        flash('Sport not found', 'error')
        return redirect(url_for('points_overview'))
    sport, table, label_gf, label_ga = result
    with get_db() as conn:
        recent_matches = [dict(e) for e in conn.execute(
            "SELECT e.*, s.name sport_name, s.icon sport_icon FROM events e "
            "LEFT JOIN sports s ON e.sport_id=s.id "
            "WHERE e.sport_id=? AND e.status='completed' "
            "ORDER BY e.event_date DESC LIMIT 10", (sport_id,)).fetchall()]
        all_sports = [dict(s) for s in conn.execute(
            "SELECT * FROM sports WHERE is_active=1 ORDER BY name").fetchall()]
    return render_template('sport_points.html', sport=sport, table=table,
                           label_gf=label_gf, label_ga=label_ga,
                           recent_matches=recent_matches, all_sports=all_sports,
                           user=current_user())




# ── LIVE MATCHES NOTIFICATION API ─────────────────────────────────────────────

@app.route('/api/live_matches')
def api_live_matches():
    """Returns all live match scores for notification polling."""
    with get_db() as conn:
        events = [dict(e) for e in conn.execute(
            "SELECT e.id, e.title, e.status, e.result, e.team1, e.team2, "
            "s.name sport_name, s.icon sport_icon "
            "FROM events e LEFT JOIN sports s ON e.sport_id=s.id "
            "WHERE e.status IN ('live','completed') ORDER BY e.event_date DESC LIMIT 30"
        ).fetchall()]
        results = []
        for ev in events:
            eid = ev['id']
            sport = (ev['sport_name'] or '').lower()
            score_t1 = score_t2 = None
            extra = ''
            try:
                if 'badminton' in sport:
                    m = conn.execute("SELECT team1_games, team2_games, id FROM badminton_matches WHERE event_id=?", (eid,)).fetchone()
                    if m:
                        score_t1 = m['team1_games']; score_t2 = m['team2_games']
                        cg = conn.execute("SELECT game_no, team1_score, team2_score FROM badminton_games WHERE match_id=? AND status='active'", (m['id'],)).fetchone()
                        if cg: extra = f"Game {cg['game_no']}: {cg['team1_score']}-{cg['team2_score']}"
                elif 'table tennis' in sport or 'tabletennis' in sport:
                    m = conn.execute("SELECT team1_games, team2_games, id FROM tabletennis_matches WHERE event_id=?", (eid,)).fetchone()
                    if m:
                        score_t1 = m['team1_games']; score_t2 = m['team2_games']
                        cg = conn.execute("SELECT game_no, team1_score, team2_score FROM tabletennis_games WHERE match_id=? AND status='active'", (m['id'],)).fetchone()
                        if cg: extra = f"Game {cg['game_no']}: {cg['team1_score']}-{cg['team2_score']}"
                elif 'cricket' in sport:
                    m = conn.execute("SELECT id FROM cricket_matches WHERE event_id=?", (eid,)).fetchone()
                    if m:
                        inns = conn.execute("SELECT batting_team, total_runs, wickets FROM cricket_innings WHERE match_id=? ORDER BY inning_no", (m['id'],)).fetchall()
                        if inns:
                            score_t1 = inns[0]['total_runs'] or 0
                            score_t2 = inns[1]['total_runs'] if len(inns) > 1 else 0
                            extra = ' | '.join(f"{i['batting_team']} {i['total_runs']}/{i['wickets']}" for i in inns)
                elif 'football' in sport or 'soccer' in sport:
                    m = conn.execute("SELECT team1_score, team2_score FROM football_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['team1_score']; score_t2 = m['team2_score']
                elif 'basketball' in sport:
                    m = conn.execute("SELECT team1_score, team2_score FROM basketball_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['team1_score']; score_t2 = m['team2_score']
                elif 'volleyball' in sport:
                    m = conn.execute("SELECT team1_sets, team2_sets FROM volleyball_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['team1_sets']; score_t2 = m['team2_sets']
                elif 'kabaddi' in sport:
                    m = conn.execute("SELECT team1_score, team2_score FROM kabaddi_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['team1_score']; score_t2 = m['team2_score']
                elif 'chess' in sport:
                    m = conn.execute("SELECT player1_score, player2_score FROM chess_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['player1_score']; score_t2 = m['player2_score']
                elif 'carrom' in sport:
                    m = conn.execute("SELECT team1_boards, team2_boards FROM carrom_matches WHERE event_id=?", (eid,)).fetchone()
                    if m: score_t1 = m['team1_boards']; score_t2 = m['team2_boards']
            except Exception:
                pass
            results.append({
                'id': eid,
                'title': ev['title'],
                'status': ev['status'],
                'result': ev['result'],
                'team1': ev['team1'],
                'team2': ev['team2'],
                'sport_name': ev['sport_name'],
                'sport_icon': ev['sport_icon'],
                'score_t1': score_t1,
                'score_t2': score_t2,
                'extra': extra,
            })
    return jsonify(results)


# ── TEAM PLAYER HISTORY (auto-fill on event create) ───────────────────────────

@app.route('/api/team/players')
@admin_required
def api_team_players():
    """Return the most recent player list for a given team + sport combination."""
    team = request.args.get('team', '').strip()
    sport_id = request.args.get('sport_id', type=int)
    if not team:
        return jsonify({'players': [], 'subs': []})
    with get_db() as conn:
        # Find the most recent event for this team + sport
        q = """SELECT e.id FROM events e
               WHERE (e.team1=? OR e.team2=?) AND e.status IN ('completed','live')
               {}
               ORDER BY e.event_date DESC, e.id DESC LIMIT 1"""
        if sport_id:
            row = conn.execute(q.format('AND e.sport_id=?'), (team, team, sport_id)).fetchone()
        else:
            row = conn.execute(q.format(''), (team, team)).fetchone()
        if not row:
            return jsonify({'players': [], 'subs': []})
        eid = row['id']
        # Determine which team key this team was
        ev = conn.execute("SELECT team1, team2 FROM events WHERE id=?", (eid,)).fetchone()
        team_key = 'team1' if ev['team1'] == team else 'team2'
        # Fetch players from event_players
        players = [dict(p) for p in conn.execute(
            "SELECT player_name, role, is_sub, player_order FROM event_players "
            "WHERE event_id=? AND team=? AND is_sub=0 ORDER BY player_order",
            (eid, team_key)).fetchall()]
        subs = [dict(p) for p in conn.execute(
            "SELECT player_name, role, is_sub, player_order FROM event_players "
            "WHERE event_id=? AND team=? AND is_sub=1 ORDER BY player_order",
            (eid, team_key)).fetchall()]
    return jsonify({'players': players, 'subs': subs, 'from_event_id': eid})


# ── SITE SETTINGS (Google Form URL etc.) ──────────────────────────────────────

def _ensure_settings_table():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS site_settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()

_ensure_settings_table()

def get_setting(key, default=''):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else default

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?,updated_at=CURRENT_TIMESTAMP",
                     (key, value, value))
        conn.commit()


@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        set_setting('gform_url', request.form.get('gform_url', '').strip())
        set_setting('gform_title', request.form.get('gform_title', '').strip())
        set_setting('gform_desc', request.form.get('gform_desc', '').strip())
        set_setting('org_name', request.form.get('org_name', '').strip())
        # API key: only overwrite if a new non-masked value provided
        flash('Settings saved!', 'success')
        return redirect(url_for('admin_settings'))
    settings = {
        'gform_url':   get_setting('gform_url'),
        'gform_title': get_setting('gform_title', 'Player Registration'),
        'gform_desc':  get_setting('gform_desc', 'Register to participate in our upcoming events.'),
        'org_name':    get_setting('org_name', 'Athena Sports'),
    }
    return render_template('admin/settings.html', settings=settings, user=current_user())


# ── PUBLIC REGISTRATION QR PAGE ───────────────────────────────────────────────

@app.route('/join')
def public_join():
    """Public player registration page — shows QR + link to Google Form."""
    settings = {
        'gform_url':   get_setting('gform_url'),
        'gform_title': get_setting('gform_title', 'Player Registration'),
        'gform_desc':  get_setting('gform_desc', 'Register to participate in our upcoming events.'),
        'org_name':    get_setting('org_name', 'Athena Sports'),
    }
    return render_template('join.html', settings=settings, user=current_user())





if __name__ == '__main__':
    # ── Development only — use start.sh (Gunicorn) for production ──
    # Set ATHENA_DEBUG=1 for hot reload during development
    debug_mode = os.environ.get('ATHENA_DEBUG', '0') == '1'
    port = int(os.environ.get('ATHENA_PORT', '5000'))
    print(f"\n{'='*55}")
    print(f"  🏆  Athena-X Sports Platform  [DEV MODE]")
    print(f"  ⚠️   Use start.sh for production (HTTPS + Gunicorn)")
    print(f"  🔒  Admin signup key : {ADMIN_SIGNUP_KEY}")
    print(f"  🌐  Running on       : http://0.0.0.0:{port}")
    print(f"  📱  Mobile access    : http://<your-ip>:{port}")
    print(f"  🔧  Debug mode       : {'ON' if debug_mode else 'OFF'}")
    print(f"{'='*55}\n")
    app.run()
