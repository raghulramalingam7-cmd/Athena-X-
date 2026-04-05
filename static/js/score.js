/* ═══════════════════════════════════════════════════════
   Athena-X Cricket — Enhanced Admin Live Scoring JS
   Features: Ball-by-ball feed, Partnership tracking,
   Wagon Wheel, NRR, Economy rates, Strike rate analysis
   ═══════════════════════════════════════════════════════ */

/* ── Toast Notification System ─────────────────────── */
(function(){
  // Inject toast container + styles once
  const style = document.createElement('style');
  style.textContent = `
    #athena-toast-container{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:99999;display:flex;flex-direction:column;gap:8px;pointer-events:none;width:min(360px,92vw);}
    .athena-toast{display:flex;align-items:center;gap:10px;padding:11px 16px;border-radius:12px;font-size:13px;font-weight:600;color:#fff;box-shadow:0 4px 20px rgba(0,0,0,0.45);animation:toastIn .25s ease;pointer-events:auto;backdrop-filter:blur(6px);}
    .athena-toast.info   {background:rgba(30,50,80,0.95);border-left:4px solid #60a5fa;}
    .athena-toast.success{background:rgba(10,50,35,0.95);border-left:4px solid #10b981;}
    .athena-toast.warning{background:rgba(60,40,0,0.95); border-left:4px solid #f59e0b;}
    .athena-toast.error  {background:rgba(60,10,10,0.95);border-left:4px solid #ef4444;}
    .athena-toast .toast-icon{font-size:18px;flex-shrink:0;}
    .athena-toast .toast-msg{flex:1;line-height:1.4;}
    .athena-toast .toast-close{cursor:pointer;opacity:0.6;font-size:16px;padding:0 2px;flex-shrink:0;}
    .athena-toast.toast-out{animation:toastOut .3s ease forwards;}
    @keyframes toastIn {from{opacity:0;transform:translateY(-12px) scale(.95);}to{opacity:1;transform:translateY(0) scale(1);}}
    @keyframes toastOut{from{opacity:1;transform:translateY(0) scale(1);}to{opacity:0;transform:translateY(-10px) scale(.95);}}
  `;
  document.head.appendChild(style);

  const container = document.createElement('div');
  container.id = 'athena-toast-container';
  document.body.appendChild(container);

  const ICONS = { info:'ℹ️', success:'✅', warning:'⚠️', error:'❌' };

  // Web Audio beep generator
  function playBeep(type) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.connect(g); g.connect(ctx.destination);
      const freqs = { success:880, info:660, warning:520, error:350 };
      o.frequency.value = freqs[type] || 660;
      o.type = type === 'error' ? 'sawtooth' : 'sine';
      g.gain.setValueAtTime(0.18, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (type==='warning'?0.35:0.22));
      o.start(ctx.currentTime);
      o.stop(ctx.currentTime + 0.35);
      ctx.close();
    } catch(e) {}
  }

  window.showToast = function(message, type='info', duration=3500) {
    playBeep(type);
    const toast = document.createElement('div');
    toast.className = 'athena-toast ' + type;
    toast.innerHTML = `<span class="toast-icon">${ICONS[type]||'🔔'}</span><span class="toast-msg">${message}</span><span class="toast-close" onclick="this.parentElement.remove()">✕</span>`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('toast-out');
      toast.addEventListener('animationend', () => toast.remove(), { once: true });
    }, duration);
  };

  // Override native alert to use toast
  window._nativeAlert = window.alert;
  window.alert = function(msg) {
    const isError = /error|invalid|duplicate|must|need|select|fail/i.test(msg);
    const isSuccess = /✅|success|recorded|substitut/i.test(msg);
    const type = isSuccess ? 'success' : isError ? 'error' : 'warning';
    window.showToast(msg, type);
  };
})();
/* ── End Toast System ───────────────────────────────── */

let matchId         = CRICKET_MATCH_ID;
let inningId        = null;
let matchState      = null;
let allPlayers      = {};
let currentOverBalls= [];
let selectedNewBatsman = null;
let selectedNewBowler  = null;
let prevScreen      = 'screen-scoring';
let lastConfirmedBowler = null; // tracks the bowler who just finished an over (no chain overs)

// Ball-by-ball feed storage
let bbbFeed = [];   // [{over, ball, runs, extra, wicket, batsman, bowler, label}]
// Partnership tracking
let partnershipStart = { runs: 0, balls: 0, bat1: '', bat2: '' };
let allDeliveries = []; // for wagon wheel

// Wagon Wheel direction feature
let wwDirectionEnabled = true;   // loaded from server
let pendingDirectionDeliveryId = null; // delivery id awaiting direction

// ── Screen helpers ────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}
function goBack() { showScreen(prevScreen); }

// ── Init ──────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  if (matchId) {
    // Load wagon wheel direction setting
    fetch(`/api/match/${matchId}/ww_settings`)
      .then(r => r.json())
      .then(s => { wwDirectionEnabled = s.ww_direction_enabled !== 0; updateWWToggleUI(); })
      .catch(() => {});

    fetch(`/api/match/${matchId}`)
      .then(r => r.json())
      .then(async state => {
        matchState = state;
        const active = state.innings.find(i => i.status === 'active');
        if (active) {
          inningId = active.id;
          await loadAllPlayers();
          await loadDeliveriesHistory();
          currentOverBalls = [];
          if (state.status === 'completed') {
            document.getElementById('result-text').textContent = state.result || 'Match completed';
            showScreen('screen-result');
          } else {
            updateScoringUI();
            showScreen('screen-scoring');
          }
        } else if (state.status === 'setup') {
          showScreen('screen-setup');
        } else {
          showScreen('screen-setup');
        }
      })
      .catch(() => showScreen('screen-setup'));
  }

  document.querySelectorAll('.modal-overlay').forEach(el => {
    el.addEventListener('click', e => {
      if (e.target === el &&
          !['modal-innings-break','modal-new-batsman','modal-new-bowler'].includes(el.id)) {
        el.style.display = 'none';
      }
    });
  });
});

// ── Load deliveries history ───────────────────────────
async function loadDeliveriesHistory() {
  if (!matchId || !inningId) return;
  try {
    const res = await fetch(`/api/match/${matchId}/deliveries?inning_id=${inningId}`);
    if (res.ok) {
      const data = await res.json();
      allDeliveries = data.deliveries || [];
      bbbFeed = allDeliveries.map(d => ({
        over: d.over_no,
        ball: d.ball_no,
        runs: d.runs,
        extra: d.extra_type,
        extraRuns: d.extra_runs,
        wicket: d.is_wicket,
        batsman: d.batsman,
        bowler: d.bowler,
        label: deliveryLabel(d)
      }));
    }
  } catch(e) { /* ok, history not critical */ }
}

function deliveryLabel(d) {
  if (d.is_wicket) return 'W';
  if (d.extra_type === 'wide') return 'Wd';
  if (d.extra_type === 'no_ball') return 'Nb';
  if (d.extra_type === 'bye') return 'By';
  if (d.extra_type === 'leg_bye') return 'LB';
  const r = (d.runs || 0) + (d.extra_runs || 0);
  return String(r);
}

// ── Ticker update ─────────────────────────────────────
function updateTicker() {
  if (!matchState) return;
  const inn = getCurrentInning();
  if (!inn) return;
  const parts = [];
  parts.push(`🏏 ${inn.batting_team}: ${inn.total_runs}/${inn.wickets} (${inn.overs_display} ov)`);
  const striker = inn.batters.find(b => b.is_on_strike === 1);
  const nonStriker = inn.batters.find(b => b.is_on_strike === 2);
  const bowler = inn.current_bowler_name
    ? (inn.bowlers.find(b => b.player_name === inn.current_bowler_name) || inn.bowlers[inn.bowlers.length - 1] || null)
    : (inn.bowlers.length ? inn.bowlers[inn.bowlers.length - 1] : null);
  if (striker) parts.push(`${striker.player_name}: ${striker.runs}(${striker.balls}) SR:${striker.strike_rate}`);
  if (nonStriker) parts.push(`${nonStriker.player_name}: ${nonStriker.runs}(${nonStriker.balls})`);
  if (bowler) parts.push(`${bowler.player_name}: ${bowler.overs_display}-${bowler.maidens}-${bowler.runs}-${bowler.wickets} Eco:${bowler.economy}`);
  if (matchState.target_info) {
    const ti = matchState.target_info;
    parts.push(`Need ${ti.runs_needed} in ${ti.overs_left} ov | RRR: ${ti.rrr}`);
  }
  const ticker = document.getElementById('ticker-text');
  if (ticker) ticker.textContent = parts.join('  ·  ');
}

// ── Setup & Toss ──────────────────────────────────────
let tossWinnerVal = '';
let batFirstVal = '';

function makeTossBtns(containerId, options, defaultVal, onSelect) {
  const cont = document.getElementById(containerId);
  if (!cont) return;
  cont.innerHTML = '';
  options.forEach(opt => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = opt.label;
    btn.dataset.value = opt.value;
    btn.style.cssText = 'padding:10px 20px;border-radius:10px;border:1px solid rgba(255,255,255,0.15);background:var(--bg3);color:var(--text);cursor:pointer;font-size:14px;font-weight:600;';
    if (opt.value === defaultVal) {
      btn.style.background = '#3b82f6'; btn.style.borderColor = '#60a5fa'; btn.style.color = 'white';
    }
    btn.addEventListener('click', function() {
      cont.querySelectorAll('button').forEach(b => { b.style.background='var(--bg3)'; b.style.borderColor='rgba(255,255,255,0.15)'; b.style.color='var(--text)'; });
      this.style.background = '#3b82f6'; this.style.borderColor = '#60a5fa'; this.style.color = 'white';
      onSelect(this.dataset.value);
    });
    cont.appendChild(btn);
  });
}

let allPlayersStructured = {};

function goToToss() {
  const t1data = (typeof collectPlayersFromUI === 'function') ? collectPlayersFromUI('team1') : null;
  const t2data = (typeof collectPlayersFromUI === 'function') ? collectPlayersFromUI('team2') : null;
  const t1p = t1data ? t1data.main.filter(p=>p.player_name).map(p=>p.player_name) : [];
  const t2p = t2data ? t2data.main.filter(p=>p.player_name).map(p=>p.player_name) : [];
  if (t1p.length < 11) return alert(`${TEAM1} needs exactly 11 players! Currently has ${t1p.length}.`);
  if (t2p.length < 11) return alert(`${TEAM2} needs exactly 11 players! Currently has ${t2p.length}.`);
  const t1set = new Set(t1p); if(t1set.size < t1p.length) return alert(`${TEAM1} has duplicate player names!`);
  const t2set = new Set(t2p); if(t2set.size < t2p.length) return alert(`${TEAM2} has duplicate player names!`);

  allPlayers[TEAM1] = t1p;
  allPlayers[TEAM2] = t2p;
  allPlayersStructured[TEAM1] = t1data || {main:t1p.map(n=>({player_name:n,role:'batsman'})),subs:[],captain:'',vc:'',wk:''};
  allPlayersStructured[TEAM2] = t2data || {main:t2p.map(n=>({player_name:n,role:'batsman'})),subs:[],captain:'',vc:'',wk:''};

  tossWinnerVal = TEAM1;
  batFirstVal = TEAM1;
  makeTossBtns('toss-options', [
    {label: '🏏 ' + TEAM1, value: TEAM1},
    {label: '🏏 ' + TEAM2, value: TEAM2}
  ], TEAM1, (v) => { tossWinnerVal = v; updateBatChoice(); });
  updateBatChoice();
  showScreen('screen-toss');
}

function updateBatChoice() {
  const winner = tossWinnerVal;
  if (!winner) return;
  const other = winner === TEAM1 ? TEAM2 : TEAM1;
  document.getElementById('bat-choice-section').style.display = 'block';
  batFirstVal = winner;
  makeTossBtns('bat-choice-options', [
    {label: '🏏 Bat', value: winner},
    {label: `⚾ Bowl (${other} bats)`, value: other}
  ], winner, (v) => { batFirstVal = v; });
}

async function startMatch() {
  const tossWinner   = tossWinnerVal;
  const battingFirst = batFirstVal;
  if (!tossWinner || !battingFirst) return alert('Complete toss selection!');
  try {
    const res = await fetch(`/api/match/new`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        team1: TEAM1, team2: TEAM2,
        total_overs: TOTAL_OVERS,
        toss_winner: tossWinner,
        batting_first: battingFirst,
        team1_players: allPlayers[TEAM1],
        team2_players: allPlayers[TEAM2],
        team1_players_structured: allPlayersStructured[TEAM1] || null,
        team2_players_structured: allPlayersStructured[TEAM2] || null,
        match_id: matchId, event_id: EVENT_ID
      })
    });
    const data = await res.json();
    matchId  = data.match_id;
    inningId = data.inning_id;
    await loadState();
    bbbFeed = [];
    allDeliveries = [];
    showBatsmenSelection();
  } catch(e) { alert('Error: ' + e.message); }
}

// ── Load state ────────────────────────────────────────
async function loadState() {
  const res  = await fetch(`/api/match/${matchId}`);
  matchState = await res.json();
}

async function loadAllPlayers() {
  const res  = await fetch(`/api/match/${matchId}/players`);
  allPlayers = await res.json();
}

function getCurrentInning() {
  return matchState?.current_inning || null;
}

// ── Batsmen selection ─────────────────────────────────
function makePlayerBtns(containerId, hiddenId, players, selectedIndex, onSelectCb) {
  const cont = document.getElementById(containerId);
  const hidden = document.getElementById(hiddenId);
  if (!cont) return;
  cont.innerHTML = '';
  let defaultVal = '';
  players.forEach((p, i) => {
    const name = typeof p === 'string' ? p : p.player_name;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = name;
    btn.style.cssText = 'padding:6px 12px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;';
    btn.addEventListener('click', function() {
      cont.querySelectorAll('button').forEach(b => { b.style.background='var(--bg3)'; b.style.borderColor='rgba(255,255,255,0.15)'; b.style.color='var(--text)'; });
      this.style.background = '#3b82f6';
      this.style.borderColor = '#60a5fa';
      this.style.color = 'white';
      hidden.value = name;
      if (onSelectCb) onSelectCb(name);
    });
    if (i === (selectedIndex || 0)) {
      btn.style.background = '#3b82f6';
      btn.style.borderColor = '#60a5fa';
      btn.style.color = 'white';
      defaultVal = name;
    }
    cont.appendChild(btn);
  });
  hidden.value = defaultVal;
}

function showBatsmenSelection() {
  const inn = getCurrentInning();
  if (!inn) return;
  document.getElementById('batting-team-label').textContent = `${inn.batting_team} Batting`;
  const batters    = inn.batters;
  const bowlTeam   = inn.bowling_team;
  const bowlPlayers= allPlayers[bowlTeam] || [];
  makePlayerBtns('striker-btns', 'select-striker', batters, 0);
  makePlayerBtns('ns-btns', 'select-non-striker', batters, 1);
  makePlayerBtns('bowler-btns', 'select-bowler', bowlPlayers, 0);
  showScreen('screen-batsmen');
}

async function confirmBatsmen() {
  const striker   = document.getElementById('select-striker').value;
  const nonStriker= document.getElementById('select-non-striker').value;
  const bowler    = document.getElementById('select-bowler').value;
  if (!striker || !nonStriker || !bowler) return alert('Select striker, non-striker and bowler!');
  if (striker === nonStriker) return alert('Striker and non-striker must be different!');
  await fetch(`/api/match/${matchId}/set_batsmen`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({striker, non_striker:nonStriker, inning_id:inningId})
  });
  await fetch(`/api/match/${matchId}/set_bowler`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({bowler, inning_id:inningId})
  });
  currentOverBalls = [];
  await loadState();
  lastConfirmedBowler = null; // reset for new innings/match
  // Reset partnership
  const inn = getCurrentInning();
  if (inn) {
    partnershipStart = { runs: inn.total_runs, balls: inn.balls, bat1: striker, bat2: nonStriker };
  }
  updateScoringUI();
  showScreen('screen-scoring');
}

// ── Partnership tracking ──────────────────────────────
function updatePartnership() {
  const inn = getCurrentInning();
  if (!inn) return;
  const striker   = inn.batters.find(b => b.is_on_strike === 1);
  const nonStriker= inn.batters.find(b => b.is_on_strike === 2);
  if (!striker || !nonStriker) return;

  // Check if partnership pair changed (wicket fell)
  if (striker.player_name !== partnershipStart.bat1 && striker.player_name !== partnershipStart.bat2 &&
      nonStriker.player_name !== partnershipStart.bat1 && nonStriker.player_name !== partnershipStart.bat2) {
    // new pair started — reset
    partnershipStart = { runs: inn.total_runs, balls: inn.balls,
                         bat1: striker.player_name, bat2: nonStriker.player_name };
  }

  const pRuns  = inn.total_runs - partnershipStart.runs;
  const pBalls = inn.balls - partnershipStart.balls;
  const pSR    = pBalls > 0 ? ((pRuns / pBalls) * 100).toFixed(0) : 0;

  const pBar = document.getElementById('partnership-bar');
  const pPlayers = document.getElementById('partnership-players');
  const pRunsEl = document.getElementById('partnership-runs');
  const pSREl = document.getElementById('partnership-sr');
  if (pBar) pBar.style.display = 'flex';
  if (pPlayers) pPlayers.textContent = `${striker.player_name} & ${nonStriker.player_name}`;
  if (pRunsEl)  pRunsEl.textContent  = `${pRuns} (${pBalls}b)`;
  if (pSREl)    pSREl.textContent    = `SR: ${pSR}`;
}

// ── Scoring UI update ─────────────────────────────────
function updateScoringUI() {
  const inn   = getCurrentInning();
  const match = matchState;
  if (!inn) return;

  document.getElementById('batting-team-name').textContent = inn.batting_team;
  document.getElementById('score-display').textContent = `${inn.total_runs}/${inn.wickets}`;
  document.getElementById('overs-display').textContent  = `Overs: ${inn.overs_display} / ${match.total_overs}`;

  const crr = inn.balls > 0 ? ((inn.total_runs / inn.balls) * 6).toFixed(2) : '0.00';
  document.getElementById('crr-display').textContent = crr;

  if (match.target_info) {
    const ti = match.target_info;
    document.getElementById('target-box').style.display = 'block';
    document.getElementById('crr-box').style.display    = 'none';
    document.getElementById('target-display').textContent = ti.target;
    document.getElementById('rrr-display').textContent    = `Need ${ti.runs_needed} in ${ti.overs_left} ov | RRR ${ti.rrr}`;
  } else {
    document.getElementById('target-box').style.display = 'none';
    document.getElementById('crr-box').style.display    = 'block';
  }

  const striker   = inn.batters.find(b => b.is_on_strike === 1);
  const nonStriker= inn.batters.find(b => b.is_on_strike === 2);
  const bowler    = inn.current_bowler_name
    ? (inn.bowlers.find(b => b.player_name === inn.current_bowler_name) || inn.bowlers[inn.bowlers.length - 1] || null)
    : (inn.bowlers.length ? inn.bowlers[inn.bowlers.length - 1] : null);

  document.getElementById('striker-name').textContent    = striker?.player_name    || '—';
  document.getElementById('striker-score').textContent   = striker ? `${striker.runs}(${striker.balls}) SR:${striker.strike_rate}` : '0(0)';
  document.getElementById('nonstriker-name').textContent  = nonStriker?.player_name || '—';
  document.getElementById('nonstriker-score').textContent = nonStriker ? `${nonStriker.runs}(${nonStriker.balls})` : '0(0)';
  document.getElementById('bowler-name-display').textContent  = bowler?.player_name || '—';
  document.getElementById('bowler-stats-display').textContent = bowler
    ? `${bowler.overs_display}-${bowler.maidens}-${bowler.runs}-${bowler.wickets} Eco:${bowler.economy}` : '0-0-0-0';

  renderOverBalls();
  renderBBBFeed();
  updatePartnership();
  updateTicker();
}

function renderOverBalls() {
  const container = document.getElementById('ball-dots');
  container.innerHTML = currentOverBalls.map(b => {
    const cls = `ball-dot b-${b}`;
    const txt = b === '0' ? '•' : b;
    return `<span class="${cls}">${txt}</span>`;
  }).join('');
}

function renderBBBFeed() {
  const container = document.getElementById('bbb-feed');
  if (!container || bbbFeed.length === 0) return;
  const recent = bbbFeed.slice(-8).reverse();
  container.innerHTML = recent.map(entry => {
    const dotCls = entry.wicket ? 'b-W' : (entry.label==='4'||entry.label==='Wd'&&entry.extraRuns>=4) ? 'b-4' : entry.label==='6' ? 'b-6' : entry.label==='Wd'||entry.label==='Nb' ? 'b-Wd' : entry.label==='0' ? 'b-0' : 'b-default';
    const desc = entry.wicket ? `💥 WICKET! ${entry.batsman} out, bowled ${entry.bowler}` :
                 entry.extra === 'wide' ? `Wide — +${(entry.extraRuns||0)+1} runs` :
                 entry.extra === 'no_ball' ? `No Ball — ${entry.runs} run${entry.runs!==1?'s':''}` :
                 entry.extra === 'bye' ? `Bye — ${entry.extraRuns} run${entry.extraRuns!==1?'s':''}` :
                 entry.extra === 'leg_bye' ? `Leg Bye — ${entry.extraRuns} run${entry.extraRuns!==1?'s':''}` :
                 entry.runs === 4 ? `FOUR! ${entry.batsman} drives for 4` :
                 entry.runs === 6 ? `SIX! ${entry.batsman} hits it out of the park!` :
                 entry.runs === 0 ? `Dot ball — ${entry.bowler} is tight` :
                 `${entry.runs} run${entry.runs!==1?'s':''} — ${entry.batsman}`;
    return `<div class="bbb-entry"><div class="bbb-row"><span class="bbb-ball ${dotCls}">${entry.label}</span><span>${entry.over+1}.${entry.ball+1} ${desc}</span></div></div>`;
  }).join('');
}

// ── Wagon Wheel Direction Picker ──────────────────────
function showDirectionPicker(runs, batsmanName, deliveryId) {
  const modal = document.getElementById('modal-direction-picker');
  if (!modal) return resumeAfterDirection(deliveryId);
  const titleEl = document.getElementById('dir-picker-title');
  if (titleEl) titleEl.textContent = runs === 6
    ? `🟣 SIX by ${batsmanName} — mark direction`
    : `🔵 FOUR by ${batsmanName} — mark direction`;
  // Reset canvas
  const canvas = document.getElementById('dir-field-canvas');
  if (canvas) {
    canvas.dataset.deliveryId = deliveryId;
    canvas.dataset.selectedAngle = '';
    drawDirectionField(canvas, null);

    function handleCanvasInput(e) {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const cx = canvas.width / 2, cy = canvas.height / 2;
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const clientY = e.touches ? e.touches[0].clientY : e.clientY;
      const x = (clientX - rect.left) * (canvas.width / rect.width) - cx;
      const y = (clientY - rect.top) * (canvas.height / rect.height) - cy;
      const angle = Math.atan2(x, -y) * 180 / Math.PI;
      canvas.dataset.selectedAngle = angle.toFixed(1);
      drawDirectionField(canvas, angle);
    }
    canvas.onclick = handleCanvasInput;
    canvas.ontouchend = handleCanvasInput;
  }
  modal.style.display = 'flex';
}

// ── Cricket fielding positions (angle from top=0, clockwise +, for RH batsman) ──
const CRICKET_POSITIONS = [
  // Infield positions
  { a:   0, d: 0.00, label: '🏏', dot: false, isWicket: true },   // Wicket (center)
  { a:  20, d: 0.42, label: 'Mid Off',      dot: true, color: 'rgba(96,165,250,0.9)' },
  { a: -20, d: 0.42, label: 'Mid On',       dot: true, color: 'rgba(96,165,250,0.9)' },
  { a:  55, d: 0.52, label: 'Cover',        dot: true, color: 'rgba(52,211,153,0.9)' },
  { a: -55, d: 0.52, label: 'Mid Wkt',      dot: true, color: 'rgba(52,211,153,0.9)' },
  { a:  88, d: 0.54, label: 'Point',        dot: true, color: 'rgba(251,191,36,0.9)' },
  { a: -88, d: 0.54, label: 'Sq Leg',       dot: true, color: 'rgba(251,191,36,0.9)' },
  { a: 110, d: 0.60, label: 'Gully',        dot: true, color: 'rgba(251,146,60,0.9)' },
  // Slip cordon
  { a: 128, d: 0.64, label: '1st Slip',     dot: true, color: 'rgba(249,115,22,0.9)' },
  { a: 140, d: 0.68, label: '2nd Slip',     dot: true, color: 'rgba(249,115,22,0.9)' },
  { a: 150, d: 0.72, label: '3rd Slip',     dot: true, color: 'rgba(239,68,68,0.8)'  },
  // Boundary / deep positions
  { a:  25, d: 0.90, label: 'Long Off',     dot: true, color: 'rgba(96,165,250,0.7)' },
  { a: -25, d: 0.90, label: 'Long On',      dot: true, color: 'rgba(96,165,250,0.7)' },
  { a:  60, d: 0.90, label: 'Deep Cover',   dot: true, color: 'rgba(52,211,153,0.7)' },
  { a: -60, d: 0.90, label: 'D Mid Wkt',    dot: true, color: 'rgba(52,211,153,0.7)' },
  { a:  90, d: 0.90, label: 'Deep Pt',      dot: true, color: 'rgba(251,191,36,0.7)' },
  { a: -90, d: 0.90, label: 'Deep Sq Leg',  dot: true, color: 'rgba(251,191,36,0.7)' },
  { a: 155, d: 0.90, label: 'Third Man',    dot: true, color: 'rgba(167,139,250,0.8)' },
  { a:-155, d: 0.90, label: 'Fine Leg',     dot: true, color: 'rgba(167,139,250,0.8)' },
];

function drawCricketPositions(ctx, cx, cy, R, showLabels, labelSize) {
  const fs = labelSize || 7.5;
  CRICKET_POSITIONS.forEach(pos => {
    if (pos.isWicket) return;
    const rad = (pos.a - 90) * Math.PI / 180;
    const px = cx + R * pos.d * Math.cos(rad);
    const py = cy + R * pos.d * Math.sin(rad);
    if (pos.dot) {
      // Dot marker
      ctx.beginPath();
      ctx.arc(px, py, pos.d >= 0.85 ? 4.5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = pos.color || 'rgba(255,255,255,0.6)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(0,0,0,0.4)';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }
    if (showLabels) {
      // Position label — nudge outward slightly for readability
      const lx = cx + R * (pos.d + (pos.d >= 0.85 ? 0.055 : 0.1)) * Math.cos(rad);
      const ly = cy + R * (pos.d + (pos.d >= 0.85 ? 0.055 : 0.1)) * Math.sin(rad);
      ctx.save();
      ctx.font = `bold ${fs}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      // Shadow for legibility
      ctx.shadowColor = 'rgba(0,0,0,0.8)';
      ctx.shadowBlur = 3;
      ctx.fillStyle = pos.color || 'rgba(255,255,255,0.7)';
      ctx.fillText(pos.label, lx, ly);
      ctx.restore();
    }
  });
}

function drawDirectionField(canvas, selectedAngle) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const cx = W/2, cy = H/2, R = W/2 - 10;
  ctx.clearRect(0,0,W,H);

  // ── Field background with gradient ──
  const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
  grad.addColorStop(0,  '#1d4a2e');
  grad.addColorStop(0.6,'#163d26');
  grad.addColorStop(1,  '#0a2014');
  ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI*2);
  ctx.fillStyle = grad; ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.18)'; ctx.lineWidth = 2; ctx.stroke();

  // ── 30° zone shading (off vs leg) ──
  // Leg side (left) tint
  ctx.save();
  ctx.beginPath();
  ctx.arc(cx, cy, R, Math.PI, Math.PI*2);
  ctx.lineTo(cx, cy); ctx.closePath();
  ctx.fillStyle = 'rgba(96,165,250,0.04)'; ctx.fill();
  ctx.restore();

  // ── Fielding circles (30-yard & boundary) ──
  [0.38, 0.70, 1.0].forEach((r, i) => {
    ctx.beginPath();
    ctx.arc(cx, cy, R * r, 0, Math.PI * 2);
    ctx.strokeStyle = i === 2 ? 'rgba(255,255,255,0.18)' : 'rgba(255,255,255,0.10)';
    ctx.lineWidth = i === 2 ? 1.5 : 1;
    ctx.setLineDash(i === 1 ? [4, 4] : []);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  // ── Sector lines ──
  for (let a = 0; a < 360; a += 30) {
    const rad = (a - 90) * Math.PI / 180;
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + R * Math.cos(rad), cy + R * Math.sin(rad));
    ctx.strokeStyle = 'rgba(255,255,255,0.05)'; ctx.lineWidth = 1; ctx.stroke();
  }

  // ── Pitch rectangle ──
  ctx.fillStyle = 'rgba(210,180,120,0.18)';
  ctx.strokeStyle = 'rgba(210,180,120,0.35)';
  ctx.lineWidth = 1;
  const pw = 8, ph = R * 0.58;
  ctx.fillRect(cx - pw/2, cy - ph/2, pw, ph);
  ctx.strokeRect(cx - pw/2, cy - ph/2, pw, ph);
  // Crease lines
  ctx.strokeStyle = 'rgba(255,255,255,0.35)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx - pw/2 - 2, cy - ph/2 + 6); ctx.lineTo(cx + pw/2 + 2, cy - ph/2 + 6); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx - pw/2 - 2, cy + ph/2 - 6); ctx.lineTo(cx + pw/2 + 2, cy + ph/2 - 6); ctx.stroke();

  // ── Off / Leg side text ──
  ctx.save();
  ctx.font = 'bold 7px sans-serif'; ctx.textAlign = 'center';
  ctx.fillStyle = 'rgba(255,255,255,0.2)';
  ctx.fillText('OFF SIDE', cx + R*0.72, cy + 10);
  ctx.fillText('LEG SIDE', cx - R*0.72, cy + 10);
  ctx.restore();

  // ── Draw cricket fielding positions ──
  drawCricketPositions(ctx, cx, cy, R, true, 7);

  // ── Batsman dot ──
  ctx.beginPath(); ctx.arc(cx, cy, 6, 0, Math.PI*2);
  ctx.fillStyle = '#fff'; ctx.fill();
  ctx.strokeStyle = '#000'; ctx.lineWidth = 1.5; ctx.stroke();

  // ── Selected direction line & arrow ──
  if (selectedAngle !== null && selectedAngle !== undefined && selectedAngle !== '') {
    const rad = (selectedAngle - 90) * Math.PI / 180;
    const ex = cx + R * 0.90 * Math.cos(rad);
    const ey = cy + R * 0.90 * Math.sin(rad);

    // Glow trail
    ctx.save();
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    ctx.strokeStyle = 'rgba(245,158,11,0.3)'; ctx.lineWidth = 8; ctx.lineCap = 'round'; ctx.stroke();
    ctx.restore();

    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey);
    ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 2.5; ctx.lineCap = 'round'; ctx.stroke();

    // Arrow head
    const aa = Math.atan2(ey - cy, ex - cx);
    ctx.beginPath();
    ctx.moveTo(ex, ey);
    ctx.lineTo(ex - 13*Math.cos(aa - 0.38), ey - 13*Math.sin(aa - 0.38));
    ctx.lineTo(ex - 13*Math.cos(aa + 0.38), ey - 13*Math.sin(aa + 0.38));
    ctx.closePath(); ctx.fillStyle = '#f59e0b'; ctx.fill();

    // End dot
    ctx.beginPath(); ctx.arc(ex, ey, 6, 0, Math.PI*2);
    ctx.fillStyle = '#f59e0b'; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();

    const instrEl = document.getElementById('dir-picker-instruction');
    if (instrEl) instrEl.textContent = 'Tap Confirm or choose again';
  } else {
    const instrEl = document.getElementById('dir-picker-instruction');
    if (instrEl) instrEl.textContent = 'Tap the field to mark shot direction';
  }
}

async function confirmDirection() {
  const canvas = document.getElementById('dir-field-canvas');
  const deliveryId = canvas?.dataset.deliveryId;
  const angle = canvas?.dataset.selectedAngle;
  if (deliveryId && angle !== '' && angle !== undefined) {
    // Update in allDeliveries array
    const d = allDeliveries.find(d => String(d.id) === String(deliveryId));
    if (d) d.shot_direction = parseFloat(angle);
    // Persist to server
    await fetch(`/api/delivery/${deliveryId}/direction`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({shot_direction: parseFloat(angle)})
    }).catch(()=>{});
  }
  closeModal('modal-direction-picker');
  await resumeAfterDirection(deliveryId);
}

async function skipDirection() {
  closeModal('modal-direction-picker');
  const canvas = document.getElementById('dir-field-canvas');
  const deliveryId = canvas?.dataset.deliveryId;
  await resumeAfterDirection(deliveryId);
}

async function resumeAfterDirection(deliveryId) {
  // Continue with match flow after direction is handled (called after 4/6 direction picker)
  const data = window._lastDeliveryData || {};
  const payload = window._lastDeliveryPayload || {};

  // Always reload state first so UI reflects the ball that was just scored
  await loadState();

  if (data.match_over) {
    showToast(`🏆 Match Over! ${data.result}`, 'success', 6000);
    document.getElementById('result-text').textContent = data.result;
    showScreen('screen-result');
    return;
  }
  if (data.new_inning) {
    const firstInn = matchState.innings.find(i => i.inning_no === 1);
    document.getElementById('innings-break-msg').textContent =
      `${firstInn?.batting_team} scored ${firstInn?.total_runs}/${firstInn?.wickets}`;
    const target = (firstInn?.total_runs || 0) + 1;
    const inn2 = getCurrentInning();
    document.getElementById('innings-break-sub').textContent =
      `${inn2?.batting_team || ''} needs ${target} to win in ${TOTAL_OVERS} overs`;
    inningId = data.new_inning_id;
    currentOverBalls = [];
    bbbFeed = [];
    allDeliveries = [];
    await loadState();
    const inn2s = getCurrentInning();
    if (inn2s) partnershipStart = { runs: 0, balls: 0, bat1: '', bat2: '' };
    openModal('modal-innings-break');
    return;
  }
  if (payload.is_wicket) {
    const ui = getCurrentInning();
    if (ui) {
      const s = ui.batters.find(b => b.is_on_strike === 1);
      const ns = ui.batters.find(b => b.is_on_strike === 2);
      partnershipStart = { runs: ui.total_runs, balls: ui.balls,
                           bat1: s?.player_name || '', bat2: ns?.player_name || '' };
      if (ui.wickets < 10) { showNewBatsmanModal(); return; }
    }
  }
  const ui = getCurrentInning();
  const ballsAfter = ui?.balls || 0;
  const ballsBefore = window._lastDeliveryBallsBefore || 0;
  const isValid = !['wide','no_ball'].includes(payload.extra_type || '');
  const endOfOver = isValid && ballsAfter > 0 && ballsAfter > ballsBefore && ballsAfter % 6 === 0;
  if (endOfOver) {
    const overJustDone = Math.floor(ballsAfter / 6);
    showToast(`✅ Over ${overJustDone} complete!`, 'success', 2500);
    currentOverBalls = [];
    showNewBowlerModal();
    return;
  }
  // Normal ball (boundary scored, no special event) — refresh the scoring UI
  updateScoringUI();
}

// ── WW Direction toggle ───────────────────────────────
function updateWWToggleUI() {
  const btn = document.getElementById('ww-toggle-btn');
  if (!btn) return;
  btn.textContent = wwDirectionEnabled ? '🟢 Direction Picker: ON' : '⚪ Direction Picker: OFF';
  btn.style.background = wwDirectionEnabled ? 'rgba(16,185,129,0.15)' : 'rgba(255,255,255,0.06)';
  btn.style.borderColor = wwDirectionEnabled ? 'rgba(16,185,129,0.4)' : 'rgba(255,255,255,0.15)';
  btn.style.color = wwDirectionEnabled ? '#10b981' : 'var(--muted)';
}

async function toggleWWDirection() {
  wwDirectionEnabled = !wwDirectionEnabled;
  updateWWToggleUI();
  await fetch(`/api/match/${matchId}/ww_settings`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({enabled: wwDirectionEnabled})
  }).catch(()=>{});
}

// ── Run buttons ───────────────────────────────────────
async function addRun(runs) {
  await postDelivery({runs, extra_type:'', extra_runs:0, is_wicket:false});
}

// ── Extras ────────────────────────────────────────────
function showExtras() {
  document.getElementById('extra-runs-val').value = '0';
  const typeVal = document.getElementById('extra-type-val');
  if (typeVal) { typeVal.value = 'wide'; }
  document.querySelectorAll('.extra-type-btn').forEach(b=>{b.style.background='var(--bg3)';b.style.borderColor='rgba(255,255,255,0.15)';b.style.color='var(--text)';});
  const wideBtn = document.querySelector('.extra-type-btn[data-val="wide"]');
  if (wideBtn) { wideBtn.style.background='#3b82f6'; wideBtn.style.borderColor='#60a5fa'; wideBtn.style.color='white'; }
  document.querySelectorAll('#modal-extras .run-btn').forEach(b => b.classList.remove('active-run'));
  document.querySelector('#modal-extras .run-btn')?.classList.add('active-run');
  openModal('modal-extras');
}

function setExtraRuns(r, btn) {
  document.getElementById('extra-runs-val').value = r;
  document.querySelectorAll('#modal-extras .run-btn').forEach(b => b.classList.remove('active-run'));
  btn.classList.add('active-run');
}

async function confirmExtras() {
  const extraType = document.getElementById('extra-type-val')?.value || 'wide';
  let extraRuns = parseInt(document.getElementById('extra-runs-val').value) || 0;
  // +1 penalty run for wide/no_ball (the automatic extra run the batting team gets)
  if (extraType === 'wide' || extraType === 'no_ball') {
    extraRuns = extraRuns + 1;
  }
  closeModal('modal-extras');
  await postDelivery({runs:0, extra_type:extraType, extra_runs:extraRuns, is_wicket:false});
}

// ── Wicket ────────────────────────────────────────────
function showWicket() {
  document.getElementById('wicket-runs-val').value = '0';
  const hiddenFielder = document.getElementById('fielder-hidden');
  if (hiddenFielder) hiddenFielder.value = '';
  const fielderBtns = document.getElementById('fielder-btns');
  if (fielderBtns) fielderBtns.innerHTML = '';
  document.querySelectorAll('#modal-wicket .run-btn').forEach(b => b.classList.remove('active-run'));
  document.querySelector('#modal-wicket .run-btn')?.classList.add('active-run');
  const typeVal = document.getElementById('wicket-type-val');
  if (typeVal) { typeVal.value = 'bowled'; }
  document.querySelectorAll('.wkt-type-btn').forEach(b=>{b.style.background='var(--bg3)';b.style.borderColor='rgba(255,255,255,0.15)';b.style.color='var(--text)';});
  const bowledBtn = document.querySelector('.wkt-type-btn[data-val="bowled"]');
  if (bowledBtn) { bowledBtn.style.background='#ef4444'; bowledBtn.style.borderColor='#f87171'; bowledBtn.style.color='white'; }
  document.getElementById('run-out-group').style.display = 'none';
  document.getElementById('fielder-group').style.display = 'none';
  const runsGroup = document.getElementById('wicket-runs-group');
  if (runsGroup) runsGroup.style.display = 'none'; // bowled is default, no runs
  openModal('modal-wicket');
}

function setWicketRuns(r, btn) {
  document.getElementById('wicket-runs-val').value = r;
  document.querySelectorAll('#modal-wicket .run-btn').forEach(b => b.classList.remove('active-run'));
  btn.classList.add('active-run');
}

async function confirmWicket() {
  const wicketType = document.getElementById('wicket-type-val')?.value || 'bowled';
  let runs = parseInt(document.getElementById('wicket-runs-val').value) || 0;
  // For these dismissals, no runs scored by the batsman count
  if (['lbw','hit_wicket','bowled','stumped','caught'].includes(wicketType)) {
    runs = 0;
  }
  const fielder = document.getElementById('fielder-input')?.value?.trim() ||
                  document.getElementById('fielder-hidden')?.value || '';
  const runOutWho = document.getElementById('run-out-who-val')?.value || 'striker';
  closeModal('modal-wicket');
  await postDelivery({runs, extra_type:'', extra_runs:0, is_wicket:true,
                      wicket_type:wicketType, fielder, run_out_batsman:runOutWho});
}

// ── Core delivery ─────────────────────────────────────
async function postDelivery(payload) {
  payload.inning_id = inningId;
  const inn         = getCurrentInning();
  const ballsBefore = inn?.balls || 0;
  const runsBefore  = inn?.total_runs || 0;

  let label;
  if (payload.is_wicket) label = 'W';
  else if (payload.extra_type === 'wide')    label = 'Wd';
  else if (payload.extra_type === 'no_ball') label = 'Nb';
  else if (payload.extra_type === 'bye')     label = 'By';
  else if (payload.extra_type === 'leg_bye') label = 'LB';
  else label = String(payload.runs + payload.extra_runs);

  const isValid = !['wide','no_ball'].includes(payload.extra_type);

  try {
    const res  = await fetch(`/api/match/${matchId}/delivery`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    // Add to ball-by-ball feed before loading new state
    const striker = inn?.batters?.find(b => b.is_on_strike === 1);
    const bowler  = inn?.current_bowler_name
      ? (inn.bowlers?.find(b => b.player_name === inn.current_bowler_name) || inn.bowlers?.[inn.bowlers.length-1] || null)
      : (inn?.bowlers?.length ? inn.bowlers[inn.bowlers.length-1] : null);
    const overNo  = Math.floor(ballsBefore / 6);
    const ballNo  = ballsBefore % 6;
    bbbFeed.push({
      over: overNo, ball: ballNo,
      runs: payload.runs, extra: payload.extra_type, extraRuns: payload.extra_runs,
      wicket: payload.is_wicket,
      batsman: striker?.player_name || '', bowler: bowler?.player_name || '',
      label
    });
    const newDelivery = {
      runs: payload.runs, extra_type: payload.extra_type, extra_runs: payload.extra_runs,
      is_wicket: payload.is_wicket, over_no: overNo, ball_no: ballNo,
      batsman: striker?.player_name || '', shot_direction: null,
      id: data.delivery_id || null
    };
    allDeliveries.push(newDelivery);

    currentOverBalls.push(label);
    await loadState();

    // Store data so resumeAfterDirection can use it
    window._lastDeliveryData = data;
    window._lastDeliveryPayload = payload;
    window._lastDeliveryBallsBefore = ballsBefore;

    // ── Toast notifications for key events ──
    const batsmanDisplay = striker?.player_name || 'Batsman';
    if (payload.is_wicket) {
      const wtype = payload.wicket_type || 'out';
      showToast(`🔴 WICKET! ${batsmanDisplay} — ${wtype.replace('_',' ')}`, 'error', 4000);
    } else if (payload.runs === 6 && !payload.extra_type) {
      showToast(`🟣 SIX! ${batsmanDisplay} hits a massive six!`, 'success', 3000);
    } else if (payload.runs === 4 && !payload.extra_type) {
      showToast(`🔵 FOUR! ${batsmanDisplay} finds the boundary!`, 'info', 2800);
    } else if (payload.extra_type === 'wide') {
      showToast('Wide ball — +1 extra', 'warning', 2000);
    } else if (payload.extra_type === 'no_ball') {
      showToast('No Ball! Free hit coming', 'warning', 2000);
    }

    // Show direction picker for 4s and 6s if feature is enabled
    const isBoundary = !payload.is_wicket && !['wide','no_ball'].includes(payload.extra_type) &&
                       (payload.runs === 4 || payload.runs === 6);
    if (wwDirectionEnabled && isBoundary && data.delivery_id) {
      pendingDirectionDeliveryId = data.delivery_id;
      const batsmanName = striker?.player_name || 'Batsman';
      showDirectionPicker(payload.runs, batsmanName, data.delivery_id);
      return; // resumeAfterDirection handles the rest
    }

    if (data.match_over) {
      showToast(`🏆 Match Over! ${data.result}`, 'success', 6000);
      document.getElementById('result-text').textContent = data.result;
      showScreen('screen-result');
      return;
    }

    if (data.new_inning) {
      const firstInn = matchState.innings.find(i => i.inning_no === 1);
      document.getElementById('innings-break-msg').textContent =
        `${firstInn?.batting_team} scored ${firstInn?.total_runs}/${firstInn?.wickets}`;
      const target = (firstInn?.total_runs || 0) + 1;
      const inn2   = getCurrentInning();
      document.getElementById('innings-break-sub').textContent =
        `${inn2?.batting_team || ''} needs ${target} to win in ${TOTAL_OVERS} overs`;
      inningId = data.new_inning_id;
      currentOverBalls = [];
      bbbFeed = [];
      allDeliveries = [];
      await loadState();
      // Reset partnership
      const inn2s = getCurrentInning();
      if (inn2s) partnershipStart = { runs: 0, balls: 0, bat1: '', bat2: '' };
      openModal('modal-innings-break');
      return;
    }

    if (payload.is_wicket) {
      const ui = getCurrentInning();
      // Reset partnership on wicket
      if (ui) {
        const s = ui.batters.find(b => b.is_on_strike === 1);
        const ns = ui.batters.find(b => b.is_on_strike === 2);
        partnershipStart = { runs: ui.total_runs, balls: ui.balls,
                             bat1: s?.player_name || '', bat2: ns?.player_name || '' };
      }
      if (ui && ui.wickets < 10) { showNewBatsmanModal(); return; }
    }

    const ui = getCurrentInning();
    const ballsAfter = ui?.balls || 0;
  const endOfOver = isValid && ballsAfter > 0 && ballsAfter > ballsBefore && ballsAfter % 6 === 0;
  if (endOfOver) {
    const overJustDone = Math.floor(ballsAfter / 6);
    showToast(`✅ Over ${overJustDone} complete!`, 'success', 2500);
    currentOverBalls = []; showNewBowlerModal(); return;
  }

    updateScoringUI();
  } catch(e) {
    console.error(e);
    alert('Error: ' + e.message);
  }
}

// ── New Batsman ───────────────────────────────────────
function showNewBatsmanModal() {
  const inn = getCurrentInning();
  if (!inn) return;
  const available = inn.batters.filter(b => !b.is_out && b.is_on_strike === 0);
  const list = document.getElementById('new-batsman-list');
  list.innerHTML = '';
  selectedNewBatsman = null;
  available.forEach(p => {
    const div = document.createElement('div');
    div.className = 'player-list-item';
    div.textContent = p.player_name;
    div.addEventListener('click', function() {
      document.querySelectorAll('#new-batsman-list .player-list-item').forEach(el => el.classList.remove('selected'));
      this.classList.add('selected');
      selectedNewBatsman = p.player_name;
    });
    list.appendChild(div);
  });
  if (!available.length) list.innerHTML = '<p style="color:var(--muted);padding:10px">No more batsmen</p>';
  openModal('modal-new-batsman');
}

async function confirmNewBatsman() {
  if (!selectedNewBatsman) return alert('Select a batsman!');
  // If the wicket fell on the last ball of an over, the new batsman enters
  // at the striker's end but must be placed as non-striker; the surviving
  // non-striker faces the first ball of the next over.
  const uiBefore = getCurrentInning();
  const isEndOfOver = !!(uiBefore && uiBefore.balls > 0 && uiBefore.balls % 6 === 0);
  await fetch(`/api/match/${matchId}/new_batsman`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({inning_id:inningId, player_name:selectedNewBatsman, end_of_over:isEndOfOver})
  });
  closeModal('modal-new-batsman');
  await loadState();
  const ui = getCurrentInning();
  if (ui && ui.balls % 6 === 0 && ui.balls > 0) {
    currentOverBalls = [];
    showNewBowlerModal();
  } else {
    updateScoringUI();
  }
}

// ── New Bowler ────────────────────────────────────────
function showNewBowlerModal() {
  const inn = getCurrentInning();
  if (!inn) return;
  const bowlTeam    = inn.bowling_team;
  const bowlPlayers = allPlayers[bowlTeam] || [];
  // Use the explicitly tracked last bowler (no chain overs allowed)
  const blockedBowler = lastConfirmedBowler ||
    inn.current_bowler_name ||
    (inn.bowlers.length ? inn.bowlers[inn.bowlers.length - 1].player_name : null);
  const list = document.getElementById('new-bowler-list');
  list.innerHTML = '';
  selectedNewBowler = null;
  let hasEligible = false;
  bowlPlayers.forEach(p => {
    if (p === blockedBowler) return; // block same bowler in consecutive overs
    hasEligible = true;
    const existing = inn.bowlers.find(b => b.player_name === p);
    const div = document.createElement('div');
    div.className = 'player-list-item';
    div.innerHTML = `${p}<span class="player-stats-sm">${existing ? `${existing.overs_display} ov · ${existing.runs}R · ${existing.wickets}W · Eco:${existing.economy}` : 'New'}</span>`;
    div.addEventListener('click', function() {
      document.querySelectorAll('#new-bowler-list .player-list-item').forEach(el => el.classList.remove('selected'));
      this.classList.add('selected');
      selectedNewBowler = p;
    });
    list.appendChild(div);
  });
  if (!hasEligible) {
    list.innerHTML = '<p style="color:var(--muted);padding:10px">No eligible bowlers (same bowler cannot bowl consecutive overs)</p>';
  }
  openModal('modal-new-bowler');
}

async function confirmNewBowler() {
  if (!selectedNewBowler) return alert('Select a bowler!');
  await fetch(`/api/match/${matchId}/set_bowler`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({inning_id:inningId, bowler:selectedNewBowler})
  });
  lastConfirmedBowler = selectedNewBowler; // track for no-chain-over rule
  closeModal('modal-new-bowler');
  await loadState();
  updateScoringUI();
}

// ── Undo last delivery ────────────────────────────────
async function undoLastDelivery() {
  if (!matchId || !inningId) return alert('No active match!');
  if (!confirm('Undo the last delivery? This will reverse all stats.')) return;
  try {
    const res = await fetch(`/api/match/${matchId}/undo_delivery`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({inning_id: inningId})
    });
    const data = await res.json();
    if (!res.ok || data.error) { alert(data.error || 'Nothing to undo'); return; }
    // Remove last entry from local feed and over balls
    bbbFeed.pop();
    allDeliveries.pop();
    if (currentOverBalls.length > 0) {
      currentOverBalls.pop();
    }
    await loadState();
    updateScoringUI();
  } catch(e) { alert('Error: ' + e.message); }
}

// ── 2nd innings ───────────────────────────────────────
async function startSecondInnings() {
  closeModal('modal-innings-break');
  await loadState();
  showBatsmenSelection();
}

// ── Analytics ─────────────────────────────────────────
function showAnalytics() {
  prevScreen = document.querySelector('.screen.active')?.id || 'screen-scoring';
  renderAnalytics();
  showScreen('screen-analytics');
}

function switchAnalyticsTab(tab) {
  document.querySelectorAll('.analytics-tab').forEach((t,i) => {
    const tabs = ['wagon','sr','eco','nrr'];
    t.classList.toggle('active', tabs[i] === tab);
  });
  document.querySelectorAll('.analytics-panel').forEach(p => p.classList.remove('active'));
  const panel = document.getElementById('panel-'+tab);
  if (panel) panel.classList.add('active');
  if (tab === 'wagon') drawWagonWheel();
}

function renderAnalytics() {
  renderStrikeRatePanel();
  renderEconomyPanel();
  renderNRRPanel();
  drawWagonWheel();
  renderRunDistribution();
}

// Wagon Wheel
function drawWagonWheel() {
  const canvas = document.getElementById('wagon-wheel-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const cx = W/2, cy = H/2, R = W/2 - 10;
  ctx.clearRect(0,0,W,H);

  // Background field
  const grad = ctx.createRadialGradient(cx,cy,0,cx,cy,R);
  grad.addColorStop(0, '#1a3a2a'); grad.addColorStop(1, '#0d2018');
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2); ctx.fillStyle=grad; ctx.fill();

  // Field circles
  [0.38, 0.70, 1.0].forEach((r, i) => {
    ctx.beginPath(); ctx.arc(cx, cy, R*r, 0, Math.PI*2);
    ctx.strokeStyle = i===2 ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.07)';
    ctx.lineWidth = 1;
    ctx.setLineDash(i===1 ? [3,3] : []);
    ctx.stroke(); ctx.setLineDash([]);
  });
  // Sector lines
  for(let a=0;a<360;a+=30) {
    ctx.beginPath(); ctx.moveTo(cx,cy);
    const rad = (a-90)*Math.PI/180;
    ctx.lineTo(cx+R*Math.cos(rad), cy+R*Math.sin(rad));
    ctx.strokeStyle='rgba(255,255,255,0.04)'; ctx.lineWidth=1; ctx.stroke();
  }
  // Pitch rectangle
  const ph = R*0.52, pw = 7;
  ctx.fillStyle='rgba(210,180,120,0.12)'; ctx.fillRect(cx-pw/2, cy-ph/2, pw, ph);
  ctx.strokeStyle='rgba(210,180,120,0.22)'; ctx.lineWidth=1; ctx.strokeRect(cx-pw/2, cy-ph/2, pw, ph);
  // Crease lines
  ctx.strokeStyle='rgba(255,255,255,0.2)'; ctx.lineWidth=0.8;
  ctx.beginPath(); ctx.moveTo(cx-pw/2-2, cy-ph/2+5); ctx.lineTo(cx+pw/2+2, cy-ph/2+5); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(cx-pw/2-2, cy+ph/2-5); ctx.lineTo(cx+pw/2+2, cy+ph/2-5); ctx.stroke();
  // Cricket fielding positions as subtle reference dots
  drawCricketPositions(ctx, cx, cy, R, false, 0);

  // Draw shots
  const delivered = allDeliveries.filter(d => !d.extra_type || !['wide','no_ball'].includes(d.extra_type));
  if (delivered.length === 0) {
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '12px sans-serif'; ctx.textAlign='center';
    ctx.fillText('No deliveries yet', cx, cy);
    return;
  }

  // Assign shot angle based on over and ball number (simulated for display)
  const shotZones = [
    {min:-60,max:60,name:'Cover'}, {min:60,max:150,name:'Leg side'},
    {min:150,max:180,name:'Fine leg'}, {min:-180,max:-150,name:'Third man'},
    {min:-150,max:-60,name:'Off side'},
  ];

  delivered.forEach((d, idx) => {
    const runs = d.runs + (d.extra_runs || 0);
    if (runs === 0 && !d.is_wicket) return;
    // Use real shot_direction if available, else deterministic pseudo-random
    let angleDeg;
    if (d.shot_direction !== null && d.shot_direction !== undefined && d.shot_direction !== '') {
      angleDeg = parseFloat(d.shot_direction);
    } else {
      const seed = (idx * 137.5 + (d.over_no||0) * 31 + (d.ball_no||0) * 7) % 360;
      angleDeg = seed - 180;
    }
    const angleRad = (angleDeg - 90) * Math.PI / 180;
    let dist, color;
    if (d.is_wicket) { color='rgba(239,68,68,0.8)'; dist=0.45; }
    else if (runs >= 6) { color='rgba(139,92,246,0.9)'; dist=0.9; }
    else if (runs === 4) { color='rgba(59,130,246,0.9)'; dist=0.85; }
    else if (runs >= 1) { color='rgba(16,185,129,0.8)'; dist=0.4+runs*0.08; }
    else { color='rgba(255,255,255,0.15)'; dist=0.2; }

    const ex = cx + R*dist*Math.cos(angleRad);
    const ey = cy + R*dist*Math.sin(angleRad);
    ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(ex,ey);
    ctx.strokeStyle=color; ctx.lineWidth = runs>=4?2:1; ctx.stroke();
    ctx.beginPath(); ctx.arc(ex,ey,runs>=4?4:2.5,0,Math.PI*2);
    ctx.fillStyle=color; ctx.fill();
  });
}

function renderRunDistribution() {
  const container = document.getElementById('run-dist-bars');
  if (!container) return;
  const counts = {0:0, 1:0, 2:0, 3:0, 4:0, 6:0, W:0};
  allDeliveries.forEach(d => {
    if (d.is_wicket) { counts.W++; return; }
    const r = d.runs;
    if (r in counts) counts[r]++; else counts[0]++;
  });
  const total = Object.values(counts).reduce((a,b)=>a+b,0) || 1;
  const labels = {0:'Dots',1:'Singles',2:'Twos',3:'Threes',4:'Fours',6:'Sixes',W:'Wickets'};
  const colors  = {0:'rgba(255,255,255,.2)',1:'rgba(16,185,129,.6)',2:'rgba(16,185,129,.8)',3:'rgba(251,191,36,.6)',4:'rgba(59,130,246,.8)',6:'rgba(139,92,246,.9)',W:'rgba(239,68,68,.8)'};
  container.innerHTML = Object.keys(counts).map(k => {
    const pct = ((counts[k]/total)*100).toFixed(0);
    return `<div class="eco-bar-container">
      <div class="eco-label">${labels[k]} (${counts[k]})</div>
      <div class="eco-track"><div class="eco-fill" style="width:${pct}%;background:${colors[k]};"></div></div>
      <div class="eco-val">${pct}%</div>
    </div>`;
  }).join('');
}

// Strike Rate Panel
function renderStrikeRatePanel() {
  const inn = getCurrentInning();
  if (!inn) return;
  const tbody = document.getElementById('sr-table-body');
  if (!tbody) return;
  const batters = inn.batters.filter(b => b.balls > 0 || b.is_on_strike);
  const maxSR = Math.max(...batters.map(b => b.strike_rate || 0), 200);
  tbody.innerHTML = batters.map(b => {
    const srPct = Math.min((b.strike_rate/maxSR)*100, 100);
    const badge = b.is_on_strike===1 ? '<span class="role-badge badge-c">ON</span>' : '';
    return `<tr>
      <td>${b.player_name}${badge}</td>
      <td><strong>${b.runs}</strong></td>
      <td>${b.balls}</td>
      <td><strong style="color:${b.strike_rate>=150?'#8b5cf6':b.strike_rate>=100?'#10b981':'#f59e0b'}">${b.strike_rate}</strong>
        <div class="sr-bar"><div class="sr-fill" style="width:${srPct}%"></div></div>
      </td>
      <td>${b.fours}/${b.sixes}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" style="color:var(--muted);text-align:center;">No data yet</td></tr>';

  // Over-by-over run rate bars
  const orrContainer = document.getElementById('over-rr-bars');
  if (orrContainer) {
    try {
      fetch(`/api/match/${matchId}/overs`)
        .then(r=>r.json())
        .then(data => {
          const curInn = (data.innings||[]).find(i=>i.inning_no===(getCurrentInning()?.inning_no||1));
          if (!curInn) return;
          const ovs = curInn.overs.filter(o=>o.runs>0||o.over<=Math.floor((getCurrentInning()?.balls||0)/6));
          orrContainer.innerHTML = ovs.slice(0,20).map(o => {
            const rr = o.runs;
            const pct = Math.min((rr/24)*100,100);
            const cls = rr<=5?'eco-good':rr<=10?'eco-avg':'eco-bad';
            return `<div class="eco-bar-container">
              <div class="eco-label">Over ${o.over}</div>
              <div class="eco-track"><div class="eco-fill ${cls}" style="width:${pct}%"></div></div>
              <div class="eco-val">${rr}</div>
            </div>`;
          }).join('');
        }).catch(()=>{});
    } catch(e){}
  }
}

// Economy Rate Panel
function renderEconomyPanel() {
  const inn = getCurrentInning();
  if (!inn) return;
  const ecoBars = document.getElementById('eco-bars');
  const bowlStats = document.getElementById('bowl-stats-body');
  const bowlers = inn.bowlers.filter(b => b.balls > 0);
  const maxEco = Math.max(...bowlers.map(b => b.economy || 0), 12);

  if (ecoBars) {
    ecoBars.innerHTML = bowlers.map(b => {
      const pct = Math.min((b.economy/maxEco)*100, 100);
      const cls = b.economy<=6?'eco-good':b.economy<=9?'eco-avg':'eco-bad';
      return `<div class="eco-bar-container">
        <div class="eco-label">${b.player_name}</div>
        <div class="eco-track"><div class="eco-fill ${cls}" style="width:${pct}%"></div></div>
        <div class="eco-val">${b.economy}</div>
      </div>`;
    }).join('') || '<div style="color:var(--muted);font-size:12px;text-align:center;">No bowlers yet</div>';
  }

  if (bowlStats) {
    bowlStats.innerHTML = bowlers.map(b => `<tr>
      <td>${b.player_name}</td>
      <td>${b.overs_display}</td><td>${b.maidens}</td>
      <td>${b.runs}</td><td><strong>${b.wickets}</strong></td>
      <td style="color:${b.economy<=6?'#10b981':b.economy<=9?'#f59e0b':'#ef4444'};font-weight:700">${b.economy}</td>
    </tr>`).join('') || '<tr><td colspan="6" style="color:var(--muted);text-align:center;">No data yet</td></tr>';
  }
}

// NRR Panel
function showNRR() {
  prevScreen = document.querySelector('.screen.active')?.id || 'screen-scoring';
  renderNRRPanel();
  // Switch to analytics on NRR tab
  switchAnalyticsTab('nrr');
  showScreen('screen-analytics');
}

function renderNRRPanel() {
  const grid = document.getElementById('nrr-grid');
  if (!grid || !matchState) return;
  const innings = matchState.innings || [];
  const inn1 = innings.find(i=>i.inning_no===1);
  const inn2 = innings.find(i=>i.inning_no===2);
  const totalOvers = matchState.total_overs;

  let cards = [];

  if (inn1) {
    const rpo1 = inn1.balls > 0 ? (inn1.total_runs / (inn1.balls/6)).toFixed(3) : '0.000';
    cards.push({label:`${inn1.batting_team} Run Rate`, val:rpo1, sub:`${inn1.total_runs}R / ${inn1.overs_display} ov`, cls:'green'});
  }
  if (inn2) {
    const rpo2 = inn2.balls > 0 ? (inn2.total_runs / (inn2.balls/6)).toFixed(3) : '0.000';
    cards.push({label:`${inn2.batting_team} Run Rate`, val:rpo2, sub:`${inn2.total_runs}R / ${inn2.overs_display} ov`, cls:'green'});
  }
  if (inn1 && inn2) {
    const rpo1 = inn1.balls>0 ? inn1.total_runs/(inn1.balls/6) : 0;
    const rpo2 = inn2.balls>0 ? inn2.total_runs/(inn2.balls/6) : 0;
    const nrrT1 = (rpo1 - rpo2).toFixed(3);
    const nrrT2 = (rpo2 - rpo1).toFixed(3);
    cards.push({label:`${inn1.batting_team} NRR`, val: (parseFloat(nrrT1)>=0?'+':'')+nrrT1, sub:`vs ${inn2.batting_team}`, cls: parseFloat(nrrT1)>=0?'green':'red'});
    cards.push({label:`${inn2.batting_team} NRR`, val: (parseFloat(nrrT2)>=0?'+':'')+nrrT2, sub:`vs ${inn1.batting_team}`, cls: parseFloat(nrrT2)>=0?'green':'red'});
  } else if (inn1) {
    const fullRPO = totalOvers>0 ? (inn1.total_runs/totalOvers).toFixed(3) : '0.000';
    cards.push({label:'Projected RPO', val:fullRPO, sub:`Target for 2nd innings`, cls:''});
  }

  // CRR if 2nd innings active
  if (inn2 && inn2.status==='active') {
    const inn = getCurrentInning();
    if (inn && matchState.target_info) {
      const ti = matchState.target_info;
      cards.push({label:'Required RR', val:String(ti.rrr), sub:`${ti.runs_needed} needed in ${ti.overs_left} ov`, cls: parseFloat(ti.rrr)>12?'red':parseFloat(ti.rrr)>8?'':'green'});
    }
  }

  grid.innerHTML = cards.map(c => `<div class="nrr-card ${c.cls}">
    <div class="nrr-card-label">${c.label}</div>
    <div class="nrr-card-val">${c.val}</div>
    <div class="nrr-card-sub">${c.sub}</div>
  </div>`).join('') || '<div style="color:var(--muted);font-size:12px;">Match data not available yet</div>';
}

// ── Scorecard ─────────────────────────────────────────
function showScoreboard() {
  prevScreen = document.querySelector('.screen.active')?.id || 'screen-scoring';
  if (!matchState) return;
  document.getElementById('sc-match-info').textContent =
    `${matchState.team1} vs ${matchState.team2} · ${matchState.total_overs} overs`;

  let html = '';
  const innAccents = ['#60a5fa', '#fb923c'];
  const innBgs = ['rgba(59,130,246,0.07)', 'rgba(249,115,22,0.07)'];
  const innBorders = ['rgba(59,130,246,0.22)', 'rgba(249,115,22,0.22)'];
  const innLabels = ['1ST INNINGS', '2ND INNINGS', '3RD INNINGS', '4TH INNINGS'];

  matchState.innings.forEach((inn, idx) => {
    const accent = innAccents[idx % 2];
    const bg = innBgs[idx % 2];
    const border = innBorders[idx % 2];
    const label = innLabels[inn.inning_no - 1] || (inn.inning_no + 'TH INNINGS');
    const statusTag = inn.status === 'completed'
      ? '<span style="background:rgba(16,185,129,0.18);color:#10b981;border-radius:6px;padding:1px 7px;font-size:10px;font-weight:800;">DONE</span>'
      : '<span style="background:rgba(239,68,68,0.18);color:#f87171;border-radius:6px;padding:1px 7px;font-size:10px;font-weight:800;animation:pulse-dot 1.5s infinite;">LIVE</span>';

    if (idx > 0) {
      html += '<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;">'
             + '<div style="flex:1;height:1px;background:rgba(255,255,255,0.08);"></div>'
             + '<span style="font-size:10px;color:rgba(255,255,255,0.2);font-weight:700;letter-spacing:1px;">INNINGS BREAK</span>'
             + '<div style="flex:1;height:1px;background:rgba(255,255,255,0.08);"></div></div>';
    }

    html += '<div class="scorecard-inning" style="background:' + bg + ';border:1px solid ' + border + ';border-radius:12px;margin:8px 16px;padding:14px 16px;">'
          + '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid ' + border + ';">'
          + '<span style="background:' + accent + ';color:#000;font-size:10px;font-weight:900;padding:2px 8px;border-radius:6px;letter-spacing:1px;">' + label + '</span>'
          + '<span style="font-size:15px;font-weight:900;color:' + accent + ';">' + inn.batting_team + '</span>'
          + '<span style="font-size:20px;font-weight:900;color:var(--text);margin-left:auto;">' + inn.total_runs + '/' + inn.wickets + '</span>'
          + '<span style="font-size:12px;color:var(--muted);">(' + inn.overs_display + ' ov)</span>'
          + statusTag
          + '</div>'
          + '<div style="font-size:11px;color:var(--muted);margin-bottom:10px;">'
          + 'Bowling: <strong style="color:var(--text);">' + inn.bowling_team + '</strong>'
          + ' &nbsp;·&nbsp; Extras: <strong style="color:var(--text);">' + (inn.extras||0) + '</strong>'
          + '</div>';

    const batters = inn.batters.filter(b => b.balls > 0 || b.is_on_strike > 0 || b.is_out);
    html += '<div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;margin-bottom:6px;">Batting</div>'
          + '<table class="sc-table"><thead><tr><th>Batsman</th><th>Dismissal</th><th>R</th><th>B</th><th>4s</th><th>6s</th><th>SR</th></tr></thead><tbody>'
          + batters.map(b => '<tr>'
              + '<td>' + b.player_name + (b.is_on_strike===1 ? ' &#x1F3CF;' : '') + '</td>'
              + '<td style="text-align:left;color:var(--muted);font-size:11px">' + (b.is_out ? b.dismissal : '<span style="color:var(--green)">not out</span>') + '</td>'
              + '<td><strong>' + b.runs + '</strong></td>'
              + '<td>' + b.balls + '</td><td>' + b.fours + '</td><td>' + b.sixes + '</td>'
              + '<td style="color:' + (b.strike_rate>=150?'#8b5cf6':b.strike_rate>=100?'#10b981':'var(--muted)') + '">' + b.strike_rate + '</td>'
              + '</tr>').join('')
          + '</tbody></table>';

    html += '<div style="font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;margin:14px 0 6px;">Bowling</div>'
          + '<table class="sc-table"><thead><tr><th>Bowler</th><th>O</th><th>M</th><th>R</th><th>W</th><th>Eco</th></tr></thead><tbody>'
          + inn.bowlers.map(b => '<tr>'
              + '<td>' + b.player_name + '</td>'
              + '<td>' + b.overs_display + '</td><td>' + b.maidens + '</td>'
              + '<td>' + b.runs + '</td><td><strong>' + b.wickets + '</strong></td>'
              + '<td style="color:' + (b.economy<=6?'#10b981':b.economy<=9?'#f59e0b':'#ef4444') + ';font-weight:700">' + b.economy + '</td>'
              + '</tr>').join('')
          + '</tbody></table>'
          + '</div>';
  });


  if (matchState.result) {
    html += `<div style="text-align:center;padding:16px 14px;font-size:18px;font-weight:800;color:var(--yellow)">🏆 ${matchState.result}</div>`;
  }
  document.getElementById('scorecard-content').innerHTML = html;
  showScreen('screen-scorecard');
}

// ── Modal helpers ─────────────────────────────────────
function openModal(id)  { document.getElementById(id).style.display = 'flex'; }
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

// ── Cricket Substitution ──────────────────────────────
let cricketSubTeam = null;
let usedSubPlayers = { [TEAM1]: [], [TEAM2]: [] };

function openCricketSub() {
  cricketSubTeam = null;
  const teamBtns = document.getElementById('sub-team-btns');
  teamBtns.innerHTML = '';
  [TEAM1, TEAM2].forEach(t => {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = t;
    btn.style.cssText = 'padding:8px 16px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;font-weight:600;';
    btn.addEventListener('click', function() {
      teamBtns.querySelectorAll('button').forEach(b => { b.style.background='var(--bg3)'; b.style.borderColor='rgba(255,255,255,0.15)'; b.style.color='var(--text)'; });
      this.style.background='#3b82f6'; this.style.borderColor='#60a5fa'; this.style.color='white';
      document.getElementById('sub-team-val').value = t;
      cricketSubTeam = t;
      populateCricketSubPlayers(t);
    });
    teamBtns.appendChild(btn);
  });
  document.getElementById('sub-out-btns').innerHTML = '';
  document.getElementById('sub-in-btns').innerHTML = '';
  document.getElementById('sub-out-val').value = '';
  document.getElementById('sub-in-val').value = '';
  openModal('modal-cricket-sub');
}

function populateCricketSubPlayers(team) {
  const teamKey = team === TEAM1 ? 'team1' : 'team2';
  const subs = (SETUP_PLAYERS[teamKey]?.subs || []).filter(p => !usedSubPlayers[team].includes(p.player_name));
  const inn = getCurrentInning();
  let outPlayers = [];
  if (inn) {
    if (inn.batting_team === team) {
      outPlayers = inn.batters.map(b => b.player_name);
    } else {
      outPlayers = allPlayers[team] || [];
    }
  } else {
    outPlayers = allPlayers[team] || [];
  }

  const outCont = document.getElementById('sub-out-btns');
  outCont.innerHTML = '';
  outPlayers.forEach(p => {
    const name = typeof p === 'string' ? p : p.player_name;
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = name;
    btn.style.cssText = 'padding:5px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:var(--bg3);color:var(--text);cursor:pointer;font-size:12px;';
    btn.addEventListener('click', function() {
      outCont.querySelectorAll('button').forEach(b => { b.style.background='var(--bg3)'; b.style.borderColor='rgba(255,255,255,0.15)'; b.style.color='var(--text)'; });
      this.style.background='#ef4444'; this.style.borderColor='#f87171'; this.style.color='white';
      document.getElementById('sub-out-val').value = name;
    });
    outCont.appendChild(btn);
  });

  const inCont = document.getElementById('sub-in-btns');
  inCont.innerHTML = '';
  if (subs.length === 0) {
    inCont.innerHTML = '<span style="color:var(--muted);font-size:12px;">No substitutes available</span>';
    return;
  }
  subs.forEach(p => {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.textContent = p.player_name;
    btn.style.cssText = 'padding:5px 10px;border-radius:8px;border:1px solid rgba(255,255,255,0.15);background:var(--bg3);color:var(--text);cursor:pointer;font-size:12px;';
    btn.addEventListener('click', function() {
      inCont.querySelectorAll('button').forEach(b => { b.style.background='var(--bg3)'; b.style.borderColor='rgba(255,255,255,0.15)'; b.style.color='var(--text)'; });
      this.style.background='#10b981'; this.style.borderColor='#34d399'; this.style.color='white';
      document.getElementById('sub-in-val').value = p.player_name;
    });
    inCont.appendChild(btn);
  });
}

function closeCricketSub() { closeModal('modal-cricket-sub'); }

async function confirmCricketSub() {
  const team = document.getElementById('sub-team-val').value;
  const outP = document.getElementById('sub-out-val').value;
  const inP  = document.getElementById('sub-in-val').value;
  if (!team || !outP || !inP) return alert('Select team, outgoing player and substitute!');
  if (!matchId) return alert('Match not started!');
  const idx = (allPlayers[team] || []).indexOf(outP);
  if (idx !== -1) allPlayers[team][idx] = inP;
  usedSubPlayers[team] = usedSubPlayers[team] || [];
  usedSubPlayers[team].push(inP);
  const inn = getCurrentInning();
  if (inn && inn.batting_team === team) {
    const batter = inn.batters.find(b => b.player_name === outP && !b.is_out);
    if (batter) {
      await fetch(`/api/match/${matchId}/new_batsman`, {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({inning_id: inningId, player_name: inP})
      });
    }
  }
  closeCricketSub();
  await loadState();
  updateScoringUI();
  alert(`✅ ${outP} → ${inP} substitution recorded`);
}
