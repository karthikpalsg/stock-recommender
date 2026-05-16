/*!
 * auth.js — StockPicks · GitHub-linked authentication
 *
 * Security model
 * ─────────────
 * Authentication is tied directly to your GitHub account:
 *   • A GitHub Personal Access Token (PAT) is required on first visit to any
 *     new device. Creating or copying a PAT requires your GitHub password +
 *     your GitHub 2FA (SMS / authenticator) — that IS the "GitHub approval".
 *   • The token is validated live against the GitHub API on every new device
 *     and re-checked every 24 hours on existing devices.
 *   • Only a token that belongs to the account "karthikpalsg" is accepted.
 *   • Revoking the token on github.com/settings/tokens instantly kills access
 *     on every device on the next validation cycle.
 *   • The raw token is stored in localStorage; it is never sent anywhere other
 *     than api.github.com over HTTPS.
 *
 * Usage
 * ─────
 * Add as the FIRST <script> in any app's <head>:
 *   <script src="auth.js"></script>
 * That's it — both apps share the same localStorage origin so one token
 * setup covers the Dashboard AND the Watchlist.
 */
(function () {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────────
  var TOKEN_KEY  = 'sp_gh_token';          // localStorage key for the PAT
  var VALID_KEY  = 'sp_validated_at';      // localStorage key for last-check timestamp
  var VALID_TTL  = 24 * 60 * 60 * 1000;   // re-validate after 24 hours
  var OWNER      = 'karthikpalsg';         // only this GitHub account is accepted
  var TOKEN_URL  = 'https://github.com/settings/tokens/new?scopes=read%3Auser&description=StockPicks+Auth';

  // ── 1. Immediately hide body — zero flash of unprotected content ─────────
  var hideEl = document.createElement('style');
  hideEl.id  = '__sp_hide';
  hideEl.textContent = 'body{visibility:hidden!important}';
  document.head.appendChild(hideEl);

  function reveal() {
    var el = document.getElementById('__sp_hide');
    if (el) el.remove();
  }

  // ── 2. Helpers ───────────────────────────────────────────────────────────
  function getToken()   { return localStorage.getItem(TOKEN_KEY) || ''; }
  function getValidAt() { return parseInt(localStorage.getItem(VALID_KEY) || '0', 10); }

  function isRecentlyValidated() {
    return getToken().length > 0 && (Date.now() - getValidAt() < VALID_TTL);
  }

  // ── 3. Global lock — called by the 🔒 header button ─────────────────────
  window.SP_LOCK = function () {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(VALID_KEY);
    location.reload();
  };

  // ── 4. GitHub API validation ─────────────────────────────────────────────
  function validateToken(token) {
    return fetch('https://api.github.com/user', {
      headers: {
        'Authorization': 'Bearer ' + token.trim(),
        'Accept':        'application/vnd.github.v3+json',
      }
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) return null;   // revoked / invalid
      if (!res.ok) throw new Error('GitHub API ' + res.status);
      return res.json();
    });
  }

  // ── 5. Main boot ─────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    var token = getToken();

    if (!token) {
      // No token ever set on this device
      showConnectScreen(null);
      return;
    }

    if (isRecentlyValidated()) {
      // Validated less than 24 h ago — show app immediately, re-check in background
      reveal();
      addLockButton();
      backgroundRevalidate(token);
      return;
    }

    // Token present but stale — must re-validate before showing content
    showLoading('Verifying your GitHub access…');
    validateToken(token)
      .then(function (user) {
        removeLoading();
        if (!user) {
          clearAuth();
          showConnectScreen('Your GitHub token has expired or been revoked.\nGenerate a new one on GitHub to continue.');
        } else if (user.login !== OWNER) {
          clearAuth();
          showConnectScreen('Access denied — this app is private to @' + OWNER + '.');
        } else {
          localStorage.setItem(VALID_KEY, String(Date.now()));
          reveal();
          addLockButton();
        }
      })
      .catch(function () {
        // Network offline — trust cached token, show app
        removeLoading();
        reveal();
        addLockButton();
      });
  });

  function clearAuth() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(VALID_KEY);
  }

  // Re-check in background after fast-path reveal (doesn't block UI)
  function backgroundRevalidate(token) {
    setTimeout(function () {
      validateToken(token)
        .then(function (user) {
          if (!user || user.login !== OWNER) {
            clearAuth();
            // Show a banner rather than a hard redirect so the user isn't surprised
            showRevokeBanner();
          } else {
            localStorage.setItem(VALID_KEY, String(Date.now()));
          }
        })
        .catch(function () { /* offline — ignore */ });
    }, 2000); // 2s delay so app content loads first
  }

  // ── 6. Lock button injected into .header ─────────────────────────────────
  function addLockButton() {
    var header = document.querySelector('.header');
    if (!header || document.getElementById('__sp_lock_btn')) return;
    header.style.position = 'relative';
    var btn = document.createElement('button');
    btn.id        = '__sp_lock_btn';
    btn.title     = 'Disconnect GitHub — requires token on next open';
    btn.innerHTML = '&#x1F512;';
    btn.style.cssText = [
      'position:absolute', 'bottom:10px', 'right:14px',
      'background:none', 'border:none', 'font-size:13px',
      'cursor:pointer', 'opacity:0.35', 'padding:4px 2px',
      '-webkit-tap-highlight-color:transparent', 'z-index:99',
    ].join(';');
    btn.addEventListener('click', function () {
      if (confirm('Disconnect this device from StockPicks?\nYou\'ll need your GitHub token to reconnect.')) {
        window.SP_LOCK();
      }
    });
    header.appendChild(btn);
  }

  // ── 7. Token-revoked banner (shown after background check fails) ──────────
  function showRevokeBanner() {
    injectGateStyles();
    var b = document.createElement('div');
    b.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'right:0', 'z-index:99998',
      'background:#ff4d4d', 'color:#0a0a0a',
      'font-family:-apple-system,sans-serif',
      'font-size:13px', 'font-weight:700',
      'padding:12px 16px', 'text-align:center',
      'padding-top:calc(12px + env(safe-area-inset-top))',
    ].join(';');
    b.textContent = 'GitHub token revoked — you\'ll be asked to reconnect on next open.';
    document.body.prepend(b);
  }

  // ── 8. Minimal loading overlay ─────────────────────────────────────────────
  function showLoading(msg) {
    injectGateStyles();
    var g = document.createElement('div');
    g.id = '__sp_gate';
    g.innerHTML = '<div class="gi">📈</div>' +
      '<div class="gn">Stock<em>Picks</em></div>' +
      '<div class="gs">' + msg + '</div>' +
      '<div class="g-spinner"></div>';
    document.body.appendChild(g);
  }

  function removeLoading() {
    var g = document.getElementById('__sp_gate');
    if (g) g.remove();
  }

  // ── 9. Connect screen ──────────────────────────────────────────────────────
  function showConnectScreen(errorMsg) {
    injectGateStyles();
    var gate = document.createElement('div');
    gate.id  = '__sp_gate';

    gate.innerHTML = [
      '<div class="gi">📈</div>',
      '<div class="gn">Stock<em>Picks</em></div>',

      '<div class="gs">',
        'This app is private.<br>',
        'Paste your GitHub Personal Access Token<br>',
        'to link this device to your GitHub account.',
      '</div>',

      errorMsg
        ? '<div class="g-prev-err">' + errorMsg.replace(/\n/g, '<br>') + '</div>'
        : '',

      '<div class="g-lbl">GitHub Personal Access Token</div>',
      '<input id="__sp_tok" type="password" placeholder="ghp_••••••••••••••••"',
        ' autocomplete="current-password" autocorrect="off" autocapitalize="off" spellcheck="false">',

      '<button class="g-btn" id="__sp_connect">Connect to GitHub</button>',
      '<div class="g-err" id="__sp_err"></div>',

      '<div class="g-steps">',
        '<div class="g-step-title">How to get a token</div>',
        '<div class="g-step">',
          '<span class="g-num">1</span>',
          '<span>Open GitHub on your phone or desktop — you\'ll need your password + 2FA (SMS or authenticator)</span>',
        '</div>',
        '<div class="g-step">',
          '<span class="g-num">2</span>',
          '<span>Go to <b>Settings → Developer Settings → Personal access tokens</b></span>',
        '</div>',
        '<div class="g-step">',
          '<span class="g-num">3</span>',
          '<span>Create a token with <b>read:user</b> scope</span>',
        '</div>',
        '<div class="g-step">',
          '<span class="g-num">4</span>',
          '<span>Copy and paste it above — this is a one-time setup on this device</span>',
        '</div>',
        '<a class="g-link" href="' + TOKEN_URL + '" target="_blank" rel="noopener">',
          '↗ Open GitHub token page',
        '</a>',
      '</div>',

      '<div class="g-foot">',
        'Token is validated live against the GitHub API.<br>',
        'Revoke it on GitHub to instantly lock all devices.<br>',
        'Only accepted for account @' + OWNER + '.',
      '</div>',
    ].join('');

    document.body.appendChild(gate);

    var input  = gate.querySelector('#__sp_tok');
    var btn    = gate.querySelector('#__sp_connect');
    var errEl  = gate.querySelector('#__sp_err');

    function setErr(msg) {
      errEl.textContent = msg;
      input.classList.add('inp-err');
      setTimeout(function () { input.classList.remove('inp-err'); }, 400);
      btn.disabled = false;
      btn.textContent = 'Connect to GitHub';
    }

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') btn.click();
    });

    btn.addEventListener('click', function () {
      var tok = input.value.replace(/\s/g, '');
      if (!tok) { setErr('Paste your GitHub token first'); return; }
      if (!tok.startsWith('ghp_') && !tok.startsWith('github_pat_')) {
        setErr('That doesn\'t look like a GitHub token (should start with ghp_)');
        return;
      }

      errEl.textContent  = '';
      btn.disabled       = true;
      btn.textContent    = 'Verifying with GitHub…';

      validateToken(tok)
        .then(function (user) {
          if (!user) {
            setErr('GitHub rejected this token — check it and try again');
          } else if (user.login !== OWNER) {
            setErr('Access denied — this app is private to @' + OWNER);
          } else {
            // Success
            localStorage.setItem(TOKEN_KEY, tok);
            localStorage.setItem(VALID_KEY, String(Date.now()));
            gate.style.transition = 'opacity 0.2s';
            gate.style.opacity    = '0';
            setTimeout(function () {
              gate.remove();
              reveal();
              addLockButton();
            }, 220);
          }
        })
        .catch(function () {
          setErr('Could not reach GitHub — check your internet connection');
        });
    });

    setTimeout(function () { input.focus(); }, 80);
  }

  // ── 10. Styles ─────────────────────────────────────────────────────────────
  function injectGateStyles() {
    if (document.getElementById('__sp_gate_css')) return;
    var s = document.createElement('style');
    s.id  = '__sp_gate_css';
    s.textContent = [
      '#__sp_gate{',
        'visibility:visible!important;',
        'position:fixed;inset:0;z-index:99999;background:#0a0a0a;',
        'display:flex;flex-direction:column;align-items:center;',
        'overflow-y:auto;-webkit-overflow-scrolling:touch;',
        'font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;',
        'padding:32px 24px;',
        'padding-top:calc(48px + env(safe-area-inset-top));',
        'padding-bottom:calc(32px + env(safe-area-inset-bottom));',
        'gap:0;',
      '}',

      /* Logo + name */
      '#__sp_gate .gi{font-size:44px;margin-bottom:6px}',
      '#__sp_gate .gn{font-size:24px;font-weight:800;color:#f0f0f0;letter-spacing:-0.4px;margin-bottom:0}',
      '#__sp_gate .gn em{color:#00c896;font-style:normal}',
      '#__sp_gate .gs{font-size:13px;color:#555;margin-top:6px;margin-bottom:20px;text-align:center;line-height:1.6;max-width:300px}',

      /* Previous error (shown on reconnect screen) */
      '#__sp_gate .g-prev-err{',
        'width:100%;max-width:320px;',
        'background:rgba(255,77,77,0.1);border:1px solid rgba(255,77,77,0.3);border-radius:10px;',
        'color:#ff4d4d;font-size:12px;padding:10px 12px;',
        'margin-bottom:16px;text-align:center;line-height:1.5;',
      '}',

      /* Label */
      '#__sp_gate .g-lbl{',
        'font-size:10px;font-weight:700;color:#444;',
        'text-transform:uppercase;letter-spacing:0.6px;',
        'margin-bottom:8px;align-self:flex-start;',
        'width:100%;max-width:320px;',
      '}',

      /* Token input */
      '#__sp_gate input{',
        'width:100%;max-width:320px;',
        'background:#1e1e1e;border:1.5px solid #2a2a2a;border-radius:12px;',
        'color:#f0f0f0;font-size:14px;letter-spacing:1px;',
        'padding:13px 14px;outline:none;margin-bottom:10px;',
        '-webkit-appearance:none;appearance:none;',
        'transition:border-color 0.15s;font-family:monospace;',
      '}',
      '#__sp_gate input:focus{border-color:#00c896}',
      '#__sp_gate input.inp-err{border-color:#ff4d4d!important;animation:g-shake 0.35s}',
      '@keyframes g-shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-8px)}75%{transform:translateX(8px)}}',

      /* Primary button */
      '#__sp_gate .g-btn{',
        'width:100%;max-width:320px;',
        'padding:15px;border:none;border-radius:12px;',
        'background:#00c896;color:#0a0a0a;',
        'font-size:15px;font-weight:800;letter-spacing:-0.1px;',
        'cursor:pointer;-webkit-appearance:none;appearance:none;',
        'transition:opacity 0.1s;margin-bottom:0;',
      '}',
      '#__sp_gate .g-btn:active{opacity:0.75}',
      '#__sp_gate .g-btn:disabled{opacity:0.45;cursor:default}',

      /* Inline error */
      '#__sp_gate .g-err{font-size:12px;color:#ff4d4d;margin-top:10px;min-height:16px;text-align:center;width:100%;max-width:320px}',

      /* Steps */
      '#__sp_gate .g-steps{',
        'width:100%;max-width:320px;',
        'background:#111;border:1px solid #222;border-radius:12px;',
        'padding:14px;margin-top:22px;',
      '}',
      '#__sp_gate .g-step-title{font-size:11px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:10px}',
      '#__sp_gate .g-step{display:flex;gap:10px;margin-bottom:9px;align-items:flex-start}',
      '#__sp_gate .g-num{',
        'flex-shrink:0;width:18px;height:18px;border-radius:50%;',
        'background:#1e1e1e;border:1px solid #2a2a2a;',
        'font-size:10px;font-weight:700;color:#555;',
        'display:flex;align-items:center;justify-content:center;margin-top:1px;',
      '}',
      '#__sp_gate .g-step span{font-size:12px;color:#555;line-height:1.5}',
      '#__sp_gate .g-step span b{color:#888;font-weight:600}',
      '#__sp_gate .g-link{',
        'display:block;margin-top:12px;',
        'font-size:12px;font-weight:700;color:#00c896;text-decoration:none;text-align:center;',
      '}',

      /* Footer note */
      '#__sp_gate .g-foot{',
        'font-size:11px;color:#2a2a2a;margin-top:20px;',
        'text-align:center;line-height:1.75;max-width:300px;',
      '}',

      /* Loading spinner */
      '#__sp_gate .g-spinner{',
        'width:28px;height:28px;margin-top:20px;',
        'border:2px solid #1e1e1e;border-top-color:#00c896;',
        'border-radius:50%;animation:sp-spin 0.75s linear infinite;',
      '}',
      '@keyframes sp-spin{to{transform:rotate(360deg)}}',
    ].join('');
    document.head.appendChild(s);
  }

})();
