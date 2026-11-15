/*
 * GRF Teamed — Selbsttest (Browser-Konsole auf grf-system.vercel.app einfügen, dann:  await teamedSelfTest())
 *
 * Testet den kompletten Ablauf gegen das echte Backend, mit 12 ERFUNDENEN Fahrern ("ZZ_selftest_01..12"):
 *   Backend erreichbar + Serveruhr | Anmeldung (PIN, doppelt, falsche PIN) | Wünsche (gültig, unbekannt, selbst,
 *   löschen) | Admin-Übersicht + Gruppenerkennung | Auslosung (Vorschau, Seed-Wiederholbarkeit, Wunschgruppe
 *   zusammen, Teamgrößen, Toleranz) | Speichern | Teamname (anzeigen, ändern, ungültig, doppelt, Admin) |
 *   Reveal-Zeitpunkt setzen/löschen | Placement-Endpunkt | Aufräumen.
 *
 * Es werden KEINE echten Fahrer angefasst: PINs/Anmeldungen/Wünsche gibt es nur für ZZ_selftest_*.
 * Die Test-Championship wird danach zurückgesetzt (Anmeldung, Teams, Reveal-Zeit). ACHTUNG: Teams, die in
 * dieser Championship schon gespeichert waren, werden dabei überschrieben und am Ende gelöscht.
 *
 * Optionen: await teamedSelfTest({ championship_id: 'teamed-test', password: '...', keep: false })
 *   keep:true  -> nicht aufräumen (zum Nachschauen im Admin-Panel/Reveal; danach selbst aufräumen mit
 *                 await teamedSelfTest({ cleanupOnly: true }))
 */
async function teamedSelfTest(opts = {}) {
  const CHAMP = opts.championship_id || 'teamed-test';
  const BASE  = opts.base || (typeof ADMIN_API_URL !== 'undefined' ? ADMIN_API_URL : '');
  const PW    = opts.password || (typeof prompt === 'function' ? prompt('Admin-Passwort (wird nicht gespeichert)') : '');
  const N = 12;
  const NAMES = Array.from({ length: N }, (_, i) => 'ZZ_selftest_' + String(i + 1).padStart(2, '0'));
  const hasSb = typeof sb === 'function';

  const results = [];
  const log = (ok, name, detail = '') => {
    results.push({ ok, name, detail });
    console.log((ok === true ? '✅' : ok === false ? '❌' : 'ℹ️ ') + ' ' + name + (detail ? '  —  ' + detail : ''));
  };
  const check = (cond, name, detail = '') => log(!!cond, name, cond ? '' : detail);
  const info  = (name, detail) => log(null, name, detail);

  async function api(method, path, body, timeoutMs = 60000) {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(BASE + path, {
        method, signal: ctl.signal,
        headers: { 'Content-Type': 'application/json', 'X-Admin-Password': PW },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data = null; try { data = await r.json(); } catch (e) {}
      return { status: r.status, ok: r.ok, data };
    } catch (e) {
      return { status: 0, ok: false, data: { error: String(e) } };
    } finally { clearTimeout(t); }
  }
  const sbRead = async (table, q) => { if (!hasSb) return null; try { return await sb(table, q); } catch (e) { return null; } };
  const sameSet = (a, b) => a.length === b.length && [...a].sort().join('|') === [...b].sort().join('|');

  const pins = {};
  let original = null, originalReveal = undefined;
  let signedUpAny = false, savedAny = false;

  async function cleanup() {
    console.log('— Aufräumen —');
    for (const n of NAMES) await api('POST', '/admin/team-signup/remove', { championship_id: CHAMP, driver_name: n });
    // Teams der Test-Championship entfernen (Speichern ohne Mitglieder, solange niemand mehr angemeldet ist)
    const left = await api('GET', '/admin/team-signup/overview?championship_id=' + encodeURIComponent(CHAMP));
    if (left.ok && (left.data.signups || []).length === 0) {
      const r = await api('POST', '/admin/team-draw/save', { championship_id: CHAMP, seed: 0, teams: [], preview: { cleanup: true } });
      info('Teams der Test-Championship gelöscht', r.ok ? 'ok' : JSON.stringify(r.data));
    } else {
      info('Teams NICHT gelöscht', 'es sind noch Anmeldungen da (echte Spieler?) — nichts angefasst');
    }
    for (const n of NAMES) await api('DELETE', '/admin/pins/' + encodeURIComponent(n));
    if (original) {
      await api('POST', '/admin/team-signup/config', {
        championship_id: CHAMP, signup_open: !!original.signup_open,
        signup_deadline: original.signup_deadline || null, team_size_pref: original.team_size_pref,
      });
    }
    if (originalReveal !== undefined) await api('POST', '/admin/team-reveal/set', { championship_id: CHAMP, reveal_at: originalReveal });
    info('Aufräumen fertig', 'Test-PINs, Anmeldungen, Wünsche, Teams, Reveal-Zeit, Anmelde-Einstellung zurückgesetzt');
  }

  if (opts.cleanupOnly) { await cleanup(); return results; }
  if (!BASE || !PW) { console.error('ADMIN_API_URL oder Passwort fehlt'); return; }

  console.log(`%cTeamed-Selbsttest  |  Championship: ${CHAMP}  |  ${BASE}`, 'font-weight:bold');
  try {
    // ── 0. Backend ────────────────────────────────────────────────────────────
    const h = await api('GET', '/health');
    check(h.ok, 'Backend erreichbar (/health)', 'Status ' + h.status);
    if (!h.ok) throw new Error('Backend nicht erreichbar — Abbruch');
    const now = await api('GET', '/teamreveal/now');
    const off = now.ok ? now.data.now_ms - Date.now() : null;
    check(now.ok && Math.abs(off) < 10 * 60 * 1000, 'Serveruhr (/teamreveal/now)', 'Abweichung ' + off + ' ms');
    if (now.ok) info('Uhr-Abweichung Browser↔Server', Math.round(off / 1000) + ' s (der Reveal rechnet das heraus)');

    // Ausgangszustand merken + Sicherheitsprüfung: keine echten Anmeldungen in dieser Championship
    const ov0 = await api('GET', '/admin/team-signup/overview?championship_id=' + encodeURIComponent(CHAMP));
    check(ov0.ok, 'Admin-Übersicht lädt (Championship existiert, Passwort stimmt)', JSON.stringify(ov0.data));
    if (!ov0.ok) throw new Error('Championship nicht gefunden/Passwort falsch — Abbruch');
    original = { signup_open: ov0.data.signup_open, signup_deadline: ov0.data.signup_deadline, team_size_pref: ov0.data.team_size_pref };
    const foreign = (ov0.data.signups || []).filter(s => !s.driver_name.startsWith('ZZ_selftest_'));
    if (foreign.length) {
      log(false, 'Sicherheitsprüfung', `In "${CHAMP}" sind ${foreign.length} echte Anmeldungen (z.B. ${foreign[0].driver_name}). Test abgebrochen, nichts verändert.`);
      return results;
    }
    const rv = await sbRead('championships', `select=reveal_at&id=eq.${encodeURIComponent(CHAMP)}&limit=1`);
    if (rv && rv[0]) originalReveal = rv[0].reveal_at || null;
    else info('reveal_at nicht lesbar', 'Spalte fehlt? team_reveal.sql ausgeführt?');

    // ── 1. Anmeldung öffnen + Test-Fahrer anlegen ─────────────────────────────
    const deadline = new Date(Date.now() + 2 * 3600 * 1000).toISOString();
    const cfg = await api('POST', '/admin/team-signup/config', { championship_id: CHAMP, signup_open: true, signup_deadline: deadline, team_size_pref: 3 });
    check(cfg.ok, 'Anmeldung öffnen + Wunschgröße 3 setzen', JSON.stringify(cfg.data));
    for (const n of NAMES) {
      const r = await api('POST', '/admin/pins/generate', { driver_name: n });
      if (!r.ok) { log(false, 'PIN erzeugen ' + n, JSON.stringify(r.data)); throw new Error('PIN-Erzeugung fehlgeschlagen'); }
      pins[n] = r.data.pin;
    }
    check(Object.keys(pins).length === N, `PINs für ${N} Test-Fahrer erzeugt (auch für Namen, die nicht in "drivers" stehen)`);
    const P = (n, extra = {}) => ({ pin: pins[n], championship_id: CHAMP, ...extra });

    // ── 2. Anmeldung ──────────────────────────────────────────────────────────
    const bad = await api('POST', '/teamsignup/register', { pin: 'falsche-pin', championship_id: CHAMP });
    check(bad.status === 404, 'Falsche PIN wird abgelehnt (404)', 'Status ' + bad.status);
    let regOk = 0, ratingKinds = new Set();
    for (const n of NAMES) {
      const r = await api('POST', '/teamsignup/register', P(n));
      if (r.ok && r.data.registered) regOk++;
      if (r.ok && r.data.rating) ratingKinds.add(r.data.rating.status);
      signedUpAny = true;
    }
    check(regOk === N, `${N} Test-Fahrer angemeldet`, regOk + ' von ' + N);
    info('Rating-Status der Test-Fahrer', [...ratingKinds].join(', ') || 'keiner (erfundene Namen → "none"/"unknown" erwartet)');
    const again = await api('POST', '/teamsignup/register', P(NAMES[0]));
    check(again.ok && again.data.registered, 'Doppelte Anmeldung ist unschädlich');
    const stt = await api('POST', '/teamsignup/status', P(NAMES[1]));
    check(stt.ok && stt.data.open === true && stt.data.registered === true, 'Status zeigt: offen + angemeldet', JSON.stringify(stt.data).slice(0, 160));

    // ── 3. Wünsche ────────────────────────────────────────────────────────────
    const w1 = await api('POST', '/teamsignup/wish', P(NAMES[0], { partner_name: NAMES[1] }));
    const w2 = await api('POST', '/teamsignup/wish', P(NAMES[1], { partner_name: NAMES[0] }));
    const w3 = await api('POST', '/teamsignup/wish', P(NAMES[2], { partner_name: NAMES[3] }));
    check(w1.ok && w2.ok && w3.ok, 'Wünsche gesetzt (01↔02 gegenseitig, 03→04 einseitig)', [w1, w2, w3].map(x => x.status).join('/'));
    const wUnknown = await api('POST', '/teamsignup/wish', P(NAMES[4], { partner_name: 'ZZ_selftest_gibt_es_nicht' }));
    check(wUnknown.status === 404, 'Wunsch auf unbekannten Fahrer → 404', 'Status ' + wUnknown.status);
    const wSelf = await api('POST', '/teamsignup/wish', P(NAMES[5], { partner_name: NAMES[5] }));
    check(wSelf.status === 400, 'Wunsch auf sich selbst → 400', 'Status ' + wSelf.status);
    await api('POST', '/teamsignup/wish', P(NAMES[6], { partner_name: NAMES[7] }));
    const wDel = await api('POST', '/teamsignup/wish/delete', P(NAMES[6]));
    const wAfter = await api('POST', '/teamsignup/status', P(NAMES[6]));
    check(wDel.ok && wAfter.ok && !wAfter.data.wish, 'Wunsch löschen funktioniert', JSON.stringify(wAfter.data).slice(0, 120));

    // ── 4. Admin-Übersicht ────────────────────────────────────────────────────
    const ov = await api('GET', '/admin/team-signup/overview?championship_id=' + encodeURIComponent(CHAMP));
    check(ov.ok && ov.data.signups.length === N, 'Admin-Übersicht zeigt alle 12 Anmeldungen', ov.ok ? ov.data.signups.length + ' gefunden' : JSON.stringify(ov.data));
    const fg = ov.ok ? JSON.stringify((ov.data.analysis || {}).firm_groups || []) : '';
    check(fg.includes(NAMES[0]) && fg.includes(NAMES[1]), 'Gegenseitiger Wunsch 01↔02 wird als feste Gruppe erkannt', fg);
    check(ov.ok && ov.data.team_size_pref === 3, 'Wunsch-Teamgröße = 3', String(ov.ok && ov.data.team_size_pref));

    // ── 5. Auslosung ──────────────────────────────────────────────────────────
    const pv = await api('POST', '/admin/team-draw/preview', { championship_id: CHAMP, seed: 4242, runs: 3 }, 240000);
    check(pv.ok, 'Auslosung Vorschau berechnet', JSON.stringify(pv.data).slice(0, 200));
    if (!pv.ok) throw new Error('Vorschau fehlgeschlagen');
    const draw = pv.data;
    const memberNames = t => t.members.map(m => (typeof m === 'string' ? m : m.name));
    const all = draw.teams.flatMap(memberNames);
    check(all.length === N && sameSet(all, NAMES), 'Jeder Angemeldete steht in genau einem Team', all.length + ' Einträge');
    const sizes = draw.teams.map(t => t.members.length);
    check(Math.max(...sizes) - Math.min(...sizes) <= 1 && sizes.reduce((a, b) => a + b, 0) === N,
      'Teamgrößen ausgeglichen', 'Größen: ' + sizes.join(','));
    const tOf = n => draw.teams.findIndex(t => memberNames(t).includes(n));
    check(tOf(NAMES[0]) === tOf(NAMES[1]), 'Wunschgruppe 01+02 landet im selben Team', `Team ${tOf(NAMES[0])} / ${tOf(NAMES[1])}`);
    info('Balancing', `max. Abweichung ${(draw.max_dev * 100).toFixed(1)} % (Toleranz 10 %), within_tolerance=${draw.within_tolerance}, Seed ${draw.seed}`);
    info('Ohne ELO (erfundene Namen)', `unrated: ${(draw.unrated || []).length} von ${N}` + (draw.placement_error ? ', Placement-Hinweis: ' + draw.placement_error : ''));
    const pv2 = await api('POST', '/admin/team-draw/preview', { championship_id: CHAMP, seed: 4242, runs: 3 }, 240000);
    const sig = d => JSON.stringify(d.teams.map(t => memberNames(t).slice().sort()).sort());
    check(pv2.ok && sig(pv2.data) === sig(draw), 'Gleicher Seed → gleiche Auslosung (nachvollziehbar)');

    // ── 6. Speichern ──────────────────────────────────────────────────────────
    const sv = await api('POST', '/admin/team-draw/save', { championship_id: CHAMP, seed: draw.seed, teams: draw.teams, preview: draw });
    savedAny = true;
    check(sv.ok && sv.data.teams && sv.data.teams.length === draw.teams.length, 'Teams gespeichert', JSON.stringify(sv.data).slice(0, 160));
    const dbTeams = await sbRead('teams', `select=id,name&championship_id=eq.${encodeURIComponent(CHAMP)}`);
    if (dbTeams) {
      check(dbTeams.length === draw.teams.length, 'Teams öffentlich lesbar (Teamed-Seite)', dbTeams.length + ' Teams');
      const mem = await sbRead('team_members', `select=driver_name&team_id=in.(${dbTeams.map(t => t.id).join(',')})`);
      check(mem && mem.length === N, 'Team-Mitglieder öffentlich lesbar', mem ? mem.length + ' Einträge' : 'nicht lesbar');
    } else info('Öffentliche Leseprüfung übersprungen', 'sb() nicht verfügbar');
    const stale = await api('POST', '/admin/team-draw/save', { championship_id: CHAMP, seed: 1, teams: [{ name: 'X', color: '#fff', members: [NAMES[0]] }], preview: {} });
    check(stale.status === 409, 'Speichern mit veralteten Teams wird abgelehnt (409)', 'Status ' + stale.status);
    // der abgelehnte Versuch darf nichts gelöscht haben
    const dbTeams2 = await sbRead('teams', `select=id&championship_id=eq.${encodeURIComponent(CHAMP)}`);
    if (dbTeams2) check(dbTeams2.length === draw.teams.length, 'Abgelehntes Speichern ändert nichts', dbTeams2.length + ' Teams');

    // ── 7. Teamnamen ──────────────────────────────────────────────────────────
    const sh = await api('POST', '/teamname/show', P(NAMES[0]));
    check(sh.ok && sh.data.team && sh.data.team.members.includes(NAMES[0]), '"Show team": Spieler sieht sein Team + Mitglieder', JSON.stringify(sh.data).slice(0, 160));
    const locked = sh.ok && !sh.data.can_rename;
    info('Umbenennen erlaubt?', locked ? 'GESPERRT: ' + sh.data.lock_reason + ' (Championship hat bereits laufende Events)' : 'ja (kein Event gestartet)');
    const other = draw.teams.find((t, i) => i !== tOf(NAMES[0]));
    const rBad1 = await api('POST', '/teamname/rename', P(NAMES[0], { name: 'x' }));
    const rBad2 = await api('POST', '/teamname/rename', P(NAMES[0], { name: 'unassigned' }));
    const rBad3 = await api('POST', '/teamname/rename', P(NAMES[0], { name: '<b>hack</b>' }));
    if (!locked) {
      check(rBad1.status === 400, 'Zu kurzer Name → 400', 'Status ' + rBad1.status);
      check(rBad2.status === 400, 'Reservierter Name "unassigned" → 400', 'Status ' + rBad2.status);
      check(rBad3.status === 400, 'Name mit Sonderzeichen/HTML → 400', 'Status ' + rBad3.status);
      const rOk = await api('POST', '/teamname/rename', P(NAMES[0], { name: 'ZZ Selftest Team' }));
      check(rOk.ok && rOk.data.team && rOk.data.team.name === 'ZZ Selftest Team', 'Gültiger Name wird übernommen', JSON.stringify(rOk.data).slice(0, 140));
      const rDup = await api('POST', '/teamname/rename', P(memberNames(other)[0], { name: 'zz selftest team' }));
      check(rDup.status === 409, 'Doppelter Teamname (auch bei anderer Groß-/Kleinschreibung) → 409', 'Status ' + rDup.status);
      const rOther = await api('POST', '/teamname/rename', P(memberNames(other)[0], { name: 'ZZ Anderes Team' }));
      check(rOther.ok, 'Spieler eines anderen Teams kann sein Team ebenfalls umbenennen', rOther.status + '');
    } else {
      check(rBad1.status === 403, 'Sperre greift: Umbenennen → 403', 'Status ' + rBad1.status);
    }
    const tid = sh.ok && sh.data.team ? sh.data.team.id : null;
    const adm = tid ? await api('POST', '/admin/team-name/set', { championship_id: CHAMP, team_id: tid, name: 'ZZ Admin Override' }) : { ok: false };
    check(adm.ok, 'Admin kann Teamnamen auch überschreiben (ignoriert Sperre)', JSON.stringify(adm.data));
    const sh2 = await api('POST', '/teamname/show', P(NAMES[0]));
    check(sh2.ok && sh2.data.team && sh2.data.team.name === 'ZZ Admin Override', 'Admin-Name erscheint beim Spieler', sh2.ok && sh2.data.team ? sh2.data.team.name : '');
    const sh3 = await api('POST', '/teamname/show', { pin: pins[NAMES[0]], championship_id: 'gibt-es-nicht-xyz' });
    check(sh3.status === 404, 'Unbekannte Championship → 404', 'Status ' + sh3.status);

    // ── 8. Reveal-Zeitpunkt ───────────────────────────────────────────────────
    const at = new Date(Date.now() + 120 * 1000).toISOString();
    const rs = await api('POST', '/admin/team-reveal/set', { championship_id: CHAMP, reveal_at: at });
    check(rs.ok, 'Reveal-Zeitpunkt setzen (+2 Min)', JSON.stringify(rs.data));
    const rv1 = await sbRead('championships', `select=reveal_at&id=eq.${encodeURIComponent(CHAMP)}&limit=1`);
    if (rv1) check(rv1[0] && Math.abs(new Date(rv1[0].reveal_at) - new Date(at)) < 1000, 'Reveal-Zeitpunkt ist öffentlich lesbar (Teamed-Seite)', JSON.stringify(rv1));
    const rsBad = await api('POST', '/admin/team-reveal/set', { championship_id: CHAMP, reveal_at: 'kein-datum' });
    check(rsBad.status === 400, 'Ungültiges Datum wird abgelehnt (400)', 'Status ' + rsBad.status);
    const rc = await api('POST', '/admin/team-reveal/set', { championship_id: CHAMP, reveal_at: null });
    check(rc.ok && rc.data.reveal_at === null, 'Reveal-Zeitpunkt löschen');
    info('Reveal-Optik/Ablauf', 'nicht automatisierbar → manuell: Admin-Panel → TEAM REVEAL → "Test: in 2 min", dann Teamed-Seite öffnen');

    // ── 9. Placement-Endpunkt ─────────────────────────────────────────────────
    const pl = await api('POST', '/admin/placement/rating', { names: [NAMES[0]] }, 150000);
    check(pl.ok && pl.data.ratings, 'Placement-Endpunkt antwortet (RaceNet-Zeiten abrufbar)', JSON.stringify(pl.data).slice(0, 160));
  } catch (e) {
    log(false, 'Test abgebrochen', String(e && e.message || e));
  } finally {
    if (opts.keep) info('Aufräumen übersprungen (keep:true)', 'später: await teamedSelfTest({ cleanupOnly: true })');
    else await cleanup();
    const bad = results.filter(r => r.ok === false), good = results.filter(r => r.ok === true);
    console.log(`%c${bad.length ? '❌' : '✅'} ${good.length} bestanden, ${bad.length} fehlgeschlagen`, 'font-weight:bold;font-size:14px');
    if (bad.length) console.table(bad.map(r => ({ Test: r.name, Details: r.detail })));
  }
  return results;
}
