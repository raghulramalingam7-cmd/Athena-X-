/* ═══════════════════════════════════════════════════════
   Gully Cricket Score Board — Frontend Logic
   ═══════════════════════════════════════════════════════ */

let matchId = null;
let inningId = null;
let matchState = null;
let allPlayers = {};
let selectedNewBatsman = null;
let selectedNewBowler = null;
let previousScreen = 'screen-scoring';
let currentOverBalls = [];

// ─────────────────────────────────────────────────────────
// SCREEN NAVIGATION
// ─────────────────────────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

function goBack() {
  showScreen(previousScreen);
}

// ─────────────────────────────────────────────────────────
// SETUP SCREEN
// ─────────────────────────────────────────────────────────
document.querySelectorAll('.over-btn').forEach(btn => {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.over-btn').forEach(b => b.classList.remove('active'));
    this.classList.add('active');
    document.getElementById('total-overs').value = this.dataset.overs;
  });
});

function goToToss() {
  const team1 = document.getElementById('team1-name').value.trim();
  const team2 = document.getElementById('team2-name').value.trim();
  const t1players = document.getElementById('team1-players').value.trim();
  const t2players = document.getElementById('team2-players').value.trim();

  if (!team1 || !team2) return alert('Please enter both team names!');
  if (!t1players || !t2players) return alert('Please enter players for both teams!');

  const t1list = t1players.split('\n').filter(p => p.trim());
  const t2list = t2players.split('\n').filter(p => p.trim());
  if (t1list.length < 2 || t2list.length < 2) return alert('Each team needs at least 2 players!');

  // Build toss radio options
  const tossDiv = document.getElementById('toss-options');
  tossDiv.innerHTML = `
    <label class="radio-item"><input type="radio" name="toss-winner" value="${team1}" checked/> 🏏 ${team1}</label>
    <label class="radio-item"><input type="radio" name="toss-winner" value="${team2}"/> 🏏 ${team2}</label>
  `;

  tossDiv.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', updateBatChoice);
  });

  updateBatChoice();
  showScreen('screen-toss');
}

function updateBatChoice() {
  const winner = document.querySelector('input[name="toss-winner"]:checked')?.value;
  if (!winner) return;
  const team1 = document.getElementById('team1-name').value.trim();
  const team2 = document.getElementById('team2-name').value.trim();
  const other = winner === team1 ? team2 : team1;

  const section = document.getElementById('bat-choice-section');
  section.style.display = 'block';
  document.getElementById('bat-choice-options').innerHTML = `
    <label class="radio-item"><input type="radio" name="bat-choice" value="${winner}" checked/> 🏏 Bat</label>
    <label class="radio-item"><input type="radio" name="bat-choice" value="${other}"/> ⚾ Bowl (${other} bats)</label>
  `;
}

async function startMatch() {
  const team1 = document.getElementById('team1-name').value.trim();
  const team2 = document.getElementById('team2-name').value.trim();
  const totalOvers = parseInt(document.getElementById('total-overs').value);
  const tossWinner = document.querySelector('input[name="toss-winner"]:checked')?.value;
  const battingFirst = document.querySelector('input[name="bat-choice"]:checked')?.value;

  if (!tossWinner || !battingFirst) return alert('Please complete toss selection!');

  const t1players = document.getElementById('team1-players').value.trim().split('\n').map(p => p.trim()).filter(Boolean);
  const t2players = document.getElementById('team2-players').value.trim().split('\n').map(p => p.trim()).filter(Boolean);

  try {
    const res = await fetch('/api/match/new', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        team1, team2, total_overs: totalOvers,
        toss_winner: tossWinner, batting_first: battingFirst,
        team1_players: t1players, team2_players: t2players
      })
    });
    const data = await res.json();
    matchId = data.match_id;
    inningId = data.inning_id;

    await loadAllPlayers();
    await loadState();
    showBatsmenSelection();
  } catch(e) {
    alert('Error starting match: ' + e.message);
  }
}

// ─────────────────────────────────────────────────────────
// LOAD STATE
// ─────────────────────────────────────────────────────────
async function loadState() {
  const res = await fetch(`/api/match/${matchId}`);
  matchState = await res.json();
  currentOverBalls = getCurrentOverBalls();
}

async function loadAllPlayers() {
  const res = await fetch(`/api/match/${matchId}/players`);
  allPlayers = await res.json();
}

function getCurrentInning() {
  return matchState?.current_inning;
}

function getCurrentOverBalls() {
  const inn = getCurrentInning();
  if (!inn) return [];
  // Reconstruct from deliveries in state (we track them via UI for now)
  return currentOverBalls;
}

// ─────────────────────────────────────────────────────────
// SELECT BATSMEN & BOWLER
// ─────────────────────────────────────────────────────────
function showBatsmenSelection() {
  const inn = getCurrentInning();
  if (!inn) return;

  document.getElementById('batting-team-label').textContent = `${inn.batting_team} Batting`;

  const batters = inn.batters;
  const bowlingTeam = inn.bowling_team;
  const bowlPlayers = allPlayers[bowlingTeam] || [];

  const strikerSel = document.getElementById('select-striker');
  const nonStrikerSel = document.getElementById('select-non-striker');
  const bowlerSel = document.getElementById('select-bowler');

  strikerSel.innerHTML = batters.map(b => `<option value="${b.player_name}">${b.player_name}</option>`).join('');
  nonStrikerSel.innerHTML = batters.map((b, i) => `<option value="${b.player_name}" ${i===1?'selected':''}>${b.player_name}</option>`).join('');
  bowlerSel.innerHTML = bowlPlayers.map(p => `<option value="${p}">${p}</option>`).join('');

  showScreen('screen-batsmen');
}

async function confirmBatsmen() {
  const striker = document.getElementById('select-striker').value;
  const nonStriker = document.getElementById('select-non-striker').value;
  const bowler = document.getElementById('select-bowler').value;

  if (striker === nonStriker) return alert('Striker and non-striker must be different players!');

  await fetch(`/api/match/${matchId}/set_batsmen`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ striker, non_striker: nonStriker, inning_id: inningId })
  });

  await fetch(`/api/match/${matchId}/set_bowler`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bowler, inning_id: inningId })
  });

  currentOverBalls = [];
  await loadState();
  updateScoringUI();
  showScreen('screen-scoring');
}

// ─────────────────────────────────────────────────────────
// SCORING SCREEN UI UPDATE
// ─────────────────────────────────────────────────────────
function updateScoringUI() {
  const inn = getCurrentInning();
  if (!inn) return;

  const match = matchState;

  // Score bar
  document.getElementById('batting-team-name').textContent = inn.batting_team;
  document.getElementById('score-display').textContent = `${inn.total_runs}/${inn.wickets}`;
  document.getElementById('overs-display').textContent = `Overs: ${inn.overs_display} / ${match.total_overs}`;

  // CRR
  const crr = inn.balls > 0 ? ((inn.total_runs / inn.balls) * 6).toFixed(2) : '0.00';
  document.getElementById('crr-display').textContent = crr;

  // Target box (2nd innings)
  if (match.target_info) {
    const ti = match.target_info;
    document.getElementById('target-box').style.display = 'block';
    document.getElementById('crr-box').style.display = 'none';
    document.getElementById('target-display').textContent = ti.target;
    document.getElementById('rrr-display').textContent = `Need ${ti.runs_needed} in ${ti.overs_left} ov`;
  } else {
    document.getElementById('target-box').style.display = 'none';
    document.getElementById('crr-box').style.display = 'block';
  }

  // Batsmen
  const striker = inn.batters.find(b => b.is_on_strike === 1);
  const nonStriker = inn.batters.find(b => b.is_on_strike === 2);
  const lastBowler = inn.bowlers.length > 0 ? inn.bowlers[inn.bowlers.length - 1] : null;

  document.getElementById('striker-name').textContent = striker?.player_name || '—';
  document.getElementById('striker-score').textContent = striker ? `${striker.runs}(${striker.balls})` : '0(0)';
  document.getElementById('nonstriker-name').textContent = nonStriker?.player_name || '—';
  document.getElementById('nonstriker-score').textContent = nonStriker ? `${nonStriker.runs}(${nonStriker.balls})` : '0(0)';

  document.getElementById('bowler-name-display').textContent = lastBowler?.player_name || '—';
  document.getElementById('bowler-stats-display').textContent = lastBowler
    ? `${lastBowler.overs_display}-${lastBowler.maidens}-${lastBowler.runs}-${lastBowler.wickets}`
    : '0-0-0-0';

  // Ball dots
  renderOverBalls();
}

function renderOverBalls() {
  const container = document.getElementById('ball-dots');
  container.innerHTML = currentOverBalls.map(b => {
    let cls = 'ball-dot';
    let txt = b;
    if (b === 'W') { cls += ' wicket'; txt = 'W'; }
    else if (b === 'Wd') { cls += ' wide'; txt = 'Wd'; }
    else if (b === 'Nb') { cls += ' no-ball'; txt = 'Nb'; }
    else if (b === 'By') { cls += ' bye'; txt = 'B'; }
    else if (b === 'LB') { cls += ' leg-bye'; txt = 'LB'; }
    else if (parseInt(b) === 0) { cls += ' dot'; txt = '•'; }
    else if (parseInt(b) === 4) { cls += ' run-4'; }
    else if (parseInt(b) === 6) { cls += ' run-6'; }
    else { cls += ` run-${b}`; }
    return `<span class="${cls}">${txt}</span>`;
  }).join('');
}

// ─────────────────────────────────────────────────────────
// ADD RUN
// ─────────────────────────────────────────────────────────
async function addRun(runs) {
  await postDelivery({ runs, extra_type: '', extra_runs: 0, is_wicket: false });
}

// ─────────────────────────────────────────────────────────
// EXTRAS
// ─────────────────────────────────────────────────────────
function showExtras() {
  document.getElementById('extra-runs-val').value = '0';
  document.querySelectorAll('.run-buttons.small .run-btn').forEach(b => b.classList.remove('active-run'));
  openModal('modal-extras');
}

function setExtraRuns(r) {
  document.getElementById('extra-runs-val').value = r;
  document.querySelectorAll('#modal-extras .run-btn').forEach(b => b.classList.remove('active-run'));
  event.target.classList.add('active-run');
}

async function confirmExtras() {
  const extraType = document.querySelector('input[name="extra-type"]:checked')?.value;
  const extraRuns = parseInt(document.getElementById('extra-runs-val').value) || 0;
  closeModal('modal-extras');
  await postDelivery({ runs: 0, extra_type: extraType, extra_runs: extraRuns, is_wicket: false });
}

// ─────────────────────────────────────────────────────────
// WICKET
// ─────────────────────────────────────────────────────────
function showWicket() {
  document.getElementById('wicket-runs-val').value = '0';
  document.getElementById('fielder-input').value = '';
  document.querySelectorAll('#modal-wicket .run-btn').forEach(b => b.classList.remove('active-run'));
  document.querySelector('#modal-wicket .run-btn')?.classList.add('active-run');
  document.getElementById('run-out-group').style.display = 'none';
  document.getElementById('fielder-group').style.display = 'block';

  document.querySelectorAll('input[name="wicket-type"]').forEach(inp => {
    inp.addEventListener('change', function() {
      document.getElementById('run-out-group').style.display = this.value === 'run_out' ? 'block' : 'none';
      document.getElementById('fielder-group').style.display = ['caught', 'stumped'].includes(this.value) ? 'block' : 'none';
    });
  });
  openModal('modal-wicket');
}

function setWicketRuns(r) {
  document.getElementById('wicket-runs-val').value = r;
  document.querySelectorAll('#modal-wicket .run-btn').forEach(b => b.classList.remove('active-run'));
  event.target.classList.add('active-run');
}

async function confirmWicket() {
  const wicketType = document.querySelector('input[name="wicket-type"]:checked')?.value;
  const runs = parseInt(document.getElementById('wicket-runs-val').value) || 0;
  const fielder = document.getElementById('fielder-input').value.trim();
  const runOutWho = document.querySelector('input[name="run-out-who"]:checked')?.value || 'striker';
  closeModal('modal-wicket');

  await postDelivery({
    runs,
    extra_type: '',
    extra_runs: 0,
    is_wicket: true,
    wicket_type: wicketType,
    fielder,
    run_out_batsman: runOutWho
  });
}

// ─────────────────────────────────────────────────────────
// POST DELIVERY
// ─────────────────────────────────────────────────────────
async function postDelivery(payload) {
  payload.inning_id = inningId;
  const inn = getCurrentInning();
  const ballsBefore = inn?.balls || 0;

  try {
    const res = await fetch(`/api/match/${matchId}/delivery`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    // Update over ball display
    const label = payload.is_wicket ? 'W'
      : payload.extra_type === 'wide' ? 'Wd'
      : payload.extra_type === 'no_ball' ? 'Nb'
      : payload.extra_type === 'bye' ? 'By'
      : payload.extra_type === 'leg_bye' ? 'LB'
      : String(payload.runs + payload.extra_runs);

    const isValid = !['wide', 'no_ball'].includes(payload.extra_type);

    if (isValid) {
      currentOverBalls.push(label);
    } else {
      currentOverBalls.push(label);  // show extras too
    }

    await loadState();

    // Check end of over (every 6 valid balls)
    const innAfter = getCurrentInning();
    const ballsAfter = innAfter?.balls || 0;
    const endOfOver = isValid && (ballsAfter % 6 === 0) && ballsAfter > 0 && ballsAfter > ballsBefore;

    if (data.match_over) {
      document.getElementById('result-text').textContent = data.result;
      showScreen('screen-result');
      return;
    }

    if (data.new_inning) {
      // Innings break
      const firstInn = matchState.innings.find(i => i.inning_no === 1);
      document.getElementById('innings-break-msg').textContent =
        `${firstInn?.batting_team} scored ${firstInn?.total_runs}/${firstInn?.wickets}`;
      const target = (firstInn?.total_runs || 0) + 1;
      document.getElementById('innings-break-sub').textContent =
        `${innAfter?.batting_team || ''} needs ${target} runs to win!`;
      inningId = data.new_inning_id;
      currentOverBalls = [];
      await loadState();
      openModal('modal-innings-break');
      return;
    }

    if (data.innings_over && !data.new_inning) {
      await loadState();
    }

    // Wicket — need new batsman
    if (payload.is_wicket) {
      await loadState();
      const updatedInn = getCurrentInning();
      if (updatedInn && updatedInn.wickets < 10) {
        showNewBatsmanModal();
        return;
      }
    }

    // End of over — need new bowler
    if (endOfOver) {
      currentOverBalls = [];
      showNewBowlerModal();
      return;
    }

    updateScoringUI();

  } catch(e) {
    console.error(e);
    alert('Error recording delivery: ' + e.message);
  }
}

// ─────────────────────────────────────────────────────────
// NEW BATSMAN MODAL
// ─────────────────────────────────────────────────────────
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
    div.innerHTML = `<span class="player-num">${p.batting_order}</span> ${p.player_name}`;
    div.addEventListener('click', function() {
      document.querySelectorAll('#new-batsman-list .player-list-item').forEach(el => el.classList.remove('selected'));
      this.classList.add('selected');
      selectedNewBatsman = p.player_name;
    });
    list.appendChild(div);
  });

  if (available.length === 0) {
    list.innerHTML = '<p style="color:var(--muted);padding:10px;">No more batsmen available.</p>';
  }
  openModal('modal-new-batsman');
}

async function confirmNewBatsman() {
  if (!selectedNewBatsman) return alert('Please select a batsman!');
  await fetch(`/api/match/${matchId}/new_batsman`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inning_id: inningId, player_name: selectedNewBatsman })
  });
  closeModal('modal-new-batsman');
  await loadState();
  updateScoringUI();

  // Check if end of over was pending
  const inn = getCurrentInning();
  if (inn && inn.balls % 6 === 0 && inn.balls > 0) {
    currentOverBalls = [];
    showNewBowlerModal();
  }
}

// ─────────────────────────────────────────────────────────
// NEW BOWLER MODAL
// ─────────────────────────────────────────────────────────
function showNewBowlerModal() {
  const inn = getCurrentInning();
  if (!inn) return;
  const bowlTeam = inn.bowling_team;
  const bowlPlayers = allPlayers[bowlTeam] || [];

  // Get last bowler to prevent consecutive
  const lastBowlerName = inn.bowlers.length > 0 ? inn.bowlers[inn.bowlers.length - 1].player_name : null;

  const list = document.getElementById('new-bowler-list');
  list.innerHTML = '';
  selectedNewBowler = null;

  bowlPlayers.forEach(p => {
    if (p === lastBowlerName) return; // can't bowl consecutive
    const div = document.createElement('div');
    div.className = 'player-list-item';
    const existing = inn.bowlers.find(b => b.player_name === p);
    const statsText = existing ? ` (${existing.overs_display} ov, ${existing.runs} runs, ${existing.wickets}W)` : ' (New)';
    div.innerHTML = `${p}<span style="color:var(--muted);font-size:12px;">${statsText}</span>`;
    div.addEventListener('click', function() {
      document.querySelectorAll('#new-bowler-list .player-list-item').forEach(el => el.classList.remove('selected'));
      this.classList.add('selected');
      selectedNewBowler = p;
    });
    list.appendChild(div);
  });

  openModal('modal-new-bowler');
}

async function confirmNewBowler() {
  if (!selectedNewBowler) return alert('Please select a bowler!');
  await fetch(`/api/match/${matchId}/set_bowler`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inning_id: inningId, bowler: selectedNewBowler })
  });
  closeModal('modal-new-bowler');
  await loadState();
  updateScoringUI();
}

// ─────────────────────────────────────────────────────────
// 2ND INNINGS START
// ─────────────────────────────────────────────────────────
async function startSecondInnings() {
  closeModal('modal-innings-break');
  await loadState();
  showBatsmenSelection();
}

// ─────────────────────────────────────────────────────────
// SCORECARD
// ─────────────────────────────────────────────────────────
function showScoreboard() {
  previousScreen = document.querySelector('.screen.active')?.id || 'screen-scoring';
  if (!matchState) return;

  const match = matchState;
  const info = `${match.team1} vs ${match.team2} • ${match.total_overs} Overs`;
  document.getElementById('sc-match-info').textContent = info;

  let html = '';

  match.innings.forEach(inn => {
    html += `
      <div class="scorecard-inning">
        <h3>${inn.batting_team} Innings — <span class="sc-score">${inn.total_runs}/${inn.wickets}</span> <span style="font-size:13px;color:var(--muted)">(${inn.overs_display} ov)</span></h3>

        <div class="sc-section-title">Batting</div>
        <table class="sc-table">
          <thead>
            <tr>
              <th>Batsman</th>
              <th>R</th>
              <th>B</th>
              <th>4s</th>
              <th>6s</th>
              <th>SR</th>
            </tr>
          </thead>
          <tbody>
            ${inn.batters.filter(b => b.balls > 0 || b.is_on_strike > 0 || b.is_out).map(b => `
              <tr>
                <td>
                  ${b.player_name}
                  ${b.is_out
                    ? `<span class="player-out-type">${formatDismissal(b)}</span>`
                    : `<span class="player-not-out">${b.is_on_strike ? '🏏 not out' : 'not out'}</span>`}
                </td>
                <td>${b.runs}</td>
                <td>${b.balls}</td>
                <td>${b.fours}</td>
                <td>${b.sixes}</td>
                <td>${b.strike_rate}</td>
              </tr>
            `).join('')}
            <tr style="font-weight:700;color:var(--muted)">
              <td colspan="6" style="text-align:right;">Extras: ${inn.extras || 0}</td>
            </tr>
          </tbody>
        </table>

        <div class="sc-section-title">Bowling</div>
        <table class="sc-table">
          <thead>
            <tr>
              <th>Bowler</th>
              <th>O</th>
              <th>M</th>
              <th>R</th>
              <th>W</th>
              <th>Eco</th>
            </tr>
          </thead>
          <tbody>
            ${inn.bowlers.map(b => `
              <tr>
                <td>${b.player_name}</td>
                <td>${b.overs_display}</td>
                <td>${b.maidens}</td>
                <td>${b.runs}</td>
                <td>${b.wickets}</td>
                <td>${b.economy}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  });

  if (match.result) {
    html += `<div class="card text-center" style="margin:0 16px 20px;"><div class="result-text">${match.result}</div></div>`;
  }

  document.getElementById('scorecard-content').innerHTML = html;
  showScreen('screen-scorecard');
}

function formatDismissal(b) {
  const type = b.out_type || '';
  const bowler = b.bowler || '';
  const map = {
    'bowled': `b ${bowler}`,
    'caught': `c & b ${bowler}`,
    'lbw': `lbw b ${bowler}`,
    'run_out': `run out (${bowler})`,
    'stumped': `st b ${bowler}`,
    'hit_wicket': `hit wkt b ${bowler}`
  };
  return map[type] || type;
}

// ─────────────────────────────────────────────────────────
// NEW MATCH
// ─────────────────────────────────────────────────────────
function newMatch() {
  matchId = null;
  inningId = null;
  matchState = null;
  allPlayers = {};
  currentOverBalls = [];
  selectedNewBatsman = null;
  selectedNewBowler = null;

  document.getElementById('team1-name').value = '';
  document.getElementById('team2-name').value = '';
  document.getElementById('team1-players').value = '';
  document.getElementById('team2-players').value = '';
  showScreen('screen-setup');
}

// ─────────────────────────────────────────────────────────
// MODAL HELPERS
// ─────────────────────────────────────────────────────────
function openModal(id) {
  document.getElementById(id).style.display = 'flex';
}

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', function(e) {
    if (e.target === this) {
      const modalsWithoutClose = ['modal-innings-break', 'modal-new-batsman', 'modal-new-bowler'];
      if (!modalsWithoutClose.includes(this.id)) {
        this.style.display = 'none';
      }
    }
  });
});

// Wicket type change handler
document.querySelectorAll('input[name="wicket-type"]').forEach(inp => {
  inp.addEventListener('change', function() {
    document.getElementById('run-out-group').style.display = this.value === 'run_out' ? 'block' : 'none';
    document.getElementById('fielder-group').style.display = ['caught', 'stumped'].includes(this.value) ? 'block' : 'none';
  });
});

// ─────────────────────────────────────────────────────────
// AUTO-LOAD MATCH (when linked from an event)
// ─────────────────────────────────────────────────────────
(async function autoLoadMatch() {
  const preloadId = window._preloadMatchId;
  if (!preloadId) return;

  try {
    const res = await fetch(`/api/match/${preloadId}`);
    if (!res.ok) return;
    const state = await res.json();
    if (!state || !state.id) return;

    matchId = state.id;
    matchState = state;

    // Load player lists
    const pres = await fetch(`/api/match/${matchId}/players`);
    allPlayers = await pres.json();

    if (state.status === 'completed') {
      // Show scorecard directly
      document.getElementById('result-text').textContent = state.result || 'Match Complete';
      showScreen('screen-result');
      showScoreboard();
    } else if (state.current_inning) {
      const inn = state.current_inning;
      inningId = inn.id;
      updateScoringUI(state);
      showScreen('screen-scoring');
    }
  } catch(e) {
    console.warn('Could not auto-load match:', e);
  }
})();
