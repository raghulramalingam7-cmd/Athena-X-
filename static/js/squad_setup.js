/**
 * squad_setup.js — Cricket-style numbered player row builder for all sports
 * Used by basketball, kabaddi, volleyball, badminton start forms
 */
(function(global){

  /**
   * SquadSetup(config)
   *   config.teams         : [{key:'team1', name:'Team A', color:'#60a5fa'}, ...]
   *   config.mainCount     : number of main players  (e.g. 7)
   *   config.subCount      : number of subs          (e.g. 5)
   *   config.positions     : [{value:'pg', label:'Point Guard'}, ...]  (optional)
   *   config.sportLabel    : e.g. '🏀 Basketball'
   *   config.preset        : {team1:{main:[{player_name,role}], subs:[...]}, team2:{...}}
   *   config.onReady       : callback when form is ready to submit (optional)
   */
  function SquadSetup(config) {
    this.cfg = config;
    this.activeTeam = config.teams[0].key;
    this._buildUI();
  }

  SquadSetup.prototype._buildUI = function() {
    const cfg = this.cfg;
    const cont = document.getElementById('squad-setup-root');
    if (!cont) return;

    // Info banner
    const mc = cfg.mainCount, sc = cfg.subCount;
    cont.innerHTML = `
      <div class="ss-info-banner">
        ${cfg.sportLabel} &nbsp;·&nbsp; Enter <strong>${mc} main players</strong> per team
        ${sc ? ` + up to <strong>${sc} substitutes</strong>` : ''}
      </div>
      <div class="ss-tabs" id="ss-tabs"></div>
      ${cfg.teams.map(t => `<div class="ss-panel" id="ss-panel-${t.key}" style="display:none;"></div>`).join('')}
    `;

    // Build tabs
    const tabsEl = document.getElementById('ss-tabs');
    cfg.teams.forEach((t, idx) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'ss-tab-' + t.key;
      btn.className = 'ss-tab' + (idx === 0 ? ' active' : '');
      btn.innerHTML = `${t.name} <span class="ss-badge" id="ss-badge-${t.key}">0/${mc}</span>`;
      btn.style.setProperty('--tab-color', t.color || '#60a5fa');
      btn.addEventListener('click', () => this.switchTab(t.key));
      tabsEl.appendChild(btn);
    });

    // Build panels
    cfg.teams.forEach(t => {
      const preset = (cfg.preset && cfg.preset[t.key]) || {main:[], subs:[]};
      this._buildPanel(t, preset);
    });

    // Show first panel
    this.switchTab(cfg.teams[0].key);
    this._updateBadges();
  };

  SquadSetup.prototype._buildPanel = function(team, preset) {
    const cfg = this.cfg;
    const panel = document.getElementById('ss-panel-' + team.key);
    if (!panel) return;

    const posHTML = cfg.positions && cfg.positions.length
      ? `<select class="ss-pos-sel">${cfg.positions.map(p=>`<option value="${p.value}">${p.label}</option>`).join('')}</select>`
      : '';

    panel.innerHTML = `
      <div class="ss-section-label" style="color:${team.color||'#60a5fa'};">${team.name} — Playing ${cfg.mainCount}</div>
      <div id="ss-main-${team.key}"></div>
      ${cfg.subCount > 0 ? `
        <div class="ss-section-label ss-sub-label">⚡ Substitutes <span style="font-weight:400;color:var(--muted);font-size:11px;">(optional — up to ${cfg.subCount})</span></div>
        <div id="ss-subs-${team.key}"></div>
      ` : ''}
    `;

    // Main rows
    const mainCont = document.getElementById('ss-main-' + team.key);
    for (let i = 1; i <= cfg.mainCount; i++) {
      const p = (preset.main || [])[i-1] || {};
      mainCont.appendChild(this._makeRow(i, false, team.key, p, cfg.positions));
    }

    // Sub rows
    if (cfg.subCount > 0) {
      const subCont = document.getElementById('ss-subs-' + team.key);
      for (let i = 1; i <= cfg.subCount; i++) {
        const p = (preset.subs || [])[i-1] || {};
        subCont.appendChild(this._makeRow(i, true, team.key, p, cfg.positions));
      }
    }
  };

  SquadSetup.prototype._makeRow = function(num, isSub, teamKey, preset, positions) {
    const row = document.createElement('div');
    row.className = 'ss-row';

    const badge = document.createElement('div');
    badge.className = 'ss-num ' + (isSub ? 'ss-num-sub' : 'ss-num-main');
    badge.textContent = isSub ? 'S'+num : num;

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'ss-inp';
    inp.placeholder = isSub ? `Sub ${num} (optional)` : `Player ${num}`;
    inp.id = `${teamKey}_${isSub?'sub':'p'}${num}_name`;
    inp.pattern = '[A-Za-z ]+';
    inp.title = 'Name must contain letters only';
    if (preset.player_name) inp.value = preset.player_name;
    inp.addEventListener('input', () => {
      inp.value = inp.value.replace(/[^A-Za-z ]/g, '');
      this._updateBadges();
    });

    row.appendChild(badge);
    row.appendChild(inp);

    if (positions && positions.length) {
      const sel = document.createElement('select');
      sel.className = 'ss-pos-sel';
      sel.id = `${teamKey}_${isSub?'sub':'p'}${num}_role`;
      positions.forEach(pos => {
        const opt = document.createElement('option');
        opt.value = pos.value;
        opt.textContent = pos.label;
        if (preset.role === pos.value) opt.selected = true;
        sel.appendChild(opt);
      });
      row.appendChild(sel);
    }

    return row;
  };

  SquadSetup.prototype._updateBadges = function() {
    const cfg = this.cfg;
    cfg.teams.forEach(t => {
      let filled = 0;
      for (let i = 1; i <= cfg.mainCount; i++) {
        const el = document.getElementById(`${t.key}_p${i}_name`);
        if (el && el.value.trim()) filled++;
      }
      const badge = document.getElementById('ss-badge-' + t.key);
      if (badge) {
        badge.textContent = `${filled}/${cfg.mainCount}`;
        badge.style.background = filled === cfg.mainCount
          ? 'rgba(34,197,94,0.35)' : 'rgba(255,255,255,0.15)';
      }
    });
  };

  SquadSetup.prototype.switchTab = function(teamKey) {
    const cfg = this.cfg;
    this.activeTeam = teamKey;
    cfg.teams.forEach(t => {
      const panel = document.getElementById('ss-panel-' + t.key);
      const tab = document.getElementById('ss-tab-' + t.key);
      if (!panel || !tab) return;
      const active = t.key === teamKey;
      panel.style.display = active ? '' : 'none';
      tab.classList.toggle('active', active);
    });
  };

  /**
   * setMainCount(n) — rebuild UI with a new mainCount (e.g. switching singles ↔ doubles)
   */
  SquadSetup.prototype.setMainCount = function(n) {
    this.cfg.mainCount = n;
    this._buildUI();
  };

  /**
   * collect() — returns {team1_players, team2_players, team1_subs, team2_subs}
   * as newline-joined strings, ready to fill hidden form fields
   */
  SquadSetup.prototype.collect = function() {
    const cfg = this.cfg;
    const result = {};
    cfg.teams.forEach(t => {
      const main = [], subs = [];
      for (let i = 1; i <= cfg.mainCount; i++) {
        const el = document.getElementById(`${t.key}_p${i}_name`);
        if (el && el.value.trim()) main.push(el.value.trim());
      }
      for (let i = 1; i <= cfg.subCount; i++) {
        const el = document.getElementById(`${t.key}_sub${i}_name`);
        if (el && el.value.trim()) subs.push(el.value.trim());
      }
      result[`${t.key}_players`] = main.join('\n');
      result[`${t.key}_subs`]    = subs.join('\n');
    });
    return result;
  };

  /**
   * validate() — checks mainCount players filled for both teams
   * returns null if OK, or error message string
   */
  SquadSetup.prototype.validate = function() {
    const cfg = this.cfg;
    for (const t of cfg.teams) {
      let filled = 0;
      for (let i = 1; i <= cfg.mainCount; i++) {
        const el = document.getElementById(`${t.key}_p${i}_name`);
        if (el && el.value.trim()) filled++;
      }
      if (filled < cfg.mainCount) {
        this.switchTab(t.key);
        return `${t.name} needs ${cfg.mainCount} players (${filled} entered)`;
      }
    }
    return null;
  };

  global.SquadSetup = SquadSetup;
})(window);
