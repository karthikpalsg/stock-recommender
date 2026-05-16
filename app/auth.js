/*!
 * auth.js — StockPicks PIN gate
 *
 * Drop-in: <script src="auth.js"></script> as the FIRST script in <head>.
 * Covers every app hosted at the same GitHub Pages origin in one login.
 *
 * First visit on a device  → "Create your 6-digit PIN" setup screen.
 * Subsequent visits         → "Enter PIN" screen (auto-skipped if already unlocked).
 * PIN is SHA-256 hashed before storage — never sent anywhere.
 * Unlock state lives in localStorage — persists until you tap 🔒 or clear browser data.
 */
(function () {
  'use strict';

  var K_HASH = 'sp_pin_h';   // SHA-256(PIN + SALT) stored after setup
  var K_OK   = 'sp_ok';      // copy of the hash written after correct unlock
  var SALT   = 'sp-karthik-stockpicks-2026';

  // ── 1. Immediately hide the page body to prevent content flash ─────────
  var hideEl = document.createElement('style');
  hideEl.id  = '__sp_hide';
  hideEl.textContent = 'body{visibility:hidden!important}';
  document.head.appendChild(hideEl);

  function reveal() {
    var el = document.getElementById('__sp_hide');
    if (el) el.remove();
  }

  // ── 2. Synchronous auth check (localStorage is sync) ──────────────────
  function storedHash()  { return localStorage.getItem(K_HASH) || ''; }
  function unlockToken() { return localStorage.getItem(K_OK)   || ''; }
  function isUnlocked()  {
    var h = storedHash();
    return h.length > 0 && h === unlockToken();
  }

  // ── 3. Expose SP_LOCK globally (used by the 🔒 header button) ─────────
  window.SP_LOCK = function () {
    localStorage.removeItem(K_OK);
    location.reload();
  };

  // ── 4. Fast-path: already authenticated ───────────────────────────────
  if (isUnlocked()) {
    document.addEventListener('DOMContentLoaded', function () {
      reveal();
      addLockButton();
    });
    return;
  }

  // ── 5. Not unlocked — show gate after DOM ready ───────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    showGate(function () {
      reveal();
      addLockButton();
    });
  });

  // ── Inject a subtle 🔒 button into the sticky .header ─────────────────
  function addLockButton() {
    var header = document.querySelector('.header');
    if (!header || document.getElementById('__sp_lock_btn')) return;
    header.style.position = 'relative';
    var btn = document.createElement('button');
    btn.id = '__sp_lock_btn';
    btn.title = 'Lock StockPicks';
    btn.textContent = '🔒';
    btn.style.cssText = 'position:absolute;bottom:10px;right:14px;background:none;border:none;' +
      'font-size:13px;cursor:pointer;opacity:0.4;padding:4px 2px;' +
      '-webkit-tap-highlight-color:transparent;z-index:99';
    btn.addEventListener('click', window.SP_LOCK);
    header.appendChild(btn);
  }

  // ── SHA-256 via Web Crypto API ─────────────────────────────────────────
  function sha256(str) {
    var data = new TextEncoder().encode(str);
    return crypto.subtle.digest('SHA-256', data).then(function (buf) {
      return Array.from(new Uint8Array(buf))
                  .map(function (b) { return b.toString(16).padStart(2, '0'); })
                  .join('');
    });
  }

  function hashPin(pin) {
    return sha256(pin.trim() + SALT);
  }

  // ── Setup: first-ever PIN creation ────────────────────────────────────
  function setupPin(pin, confirm) {
    return new Promise(function (resolve, reject) {
      if (!pin || pin.length !== 6 || !/^\d{6}$/.test(pin)) {
        return reject(new Error('PIN must be exactly 6 digits'));
      }
      if (pin !== confirm) {
        return reject(new Error('PINs don\'t match — try again'));
      }
      hashPin(pin).then(function (h) {
        localStorage.setItem(K_HASH, h);
        localStorage.setItem(K_OK,   h);
        resolve();
      });
    });
  }

  // ── Unlock: subsequent visits ─────────────────────────────────────────
  function tryUnlock(pin) {
    return hashPin(pin).then(function (h) {
      if (h === storedHash()) {
        localStorage.setItem(K_OK, h);
        return true;
      }
      return false;
    });
  }

  // ── Gate CSS (injected once) ───────────────────────────────────────────
  function injectGateStyles() {
    if (document.getElementById('__sp_gate_css')) return;
    var s = document.createElement('style');
    s.id = '__sp_gate_css';
    s.textContent = [
      /* Gate overlay */
      '#__sp_gate{',
        'visibility:visible!important;',
        'position:fixed;inset:0;z-index:99999;',
        'background:#0a0a0a;',
        'display:flex;flex-direction:column;align-items:center;justify-content:center;',
        'font-family:-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;',
        'padding:32px;',
        'padding-top:calc(56px + env(safe-area-inset-top));',
        'padding-bottom:calc(32px + env(safe-area-inset-bottom));',
        'gap:0;',
      '}',
      '#__sp_gate .gi{font-size:48px;margin-bottom:8px}',
      '#__sp_gate .gn{font-size:26px;font-weight:800;color:#f0f0f0;letter-spacing:-0.5px}',
      '#__sp_gate .gn em{color:#00c896;font-style:normal}',
      '#__sp_gate .gs{font-size:13px;color:#555;margin-top:5px;margin-bottom:32px;text-align:center;line-height:1.55;max-width:280px}',
      '#__sp_gate .gl{font-size:10px;font-weight:700;color:#444;text-transform:uppercase;letter-spacing:0.6px;margin-bottom:8px;align-self:flex-start;width:100%;max-width:300px}',

      /* PIN dot display (shows filled/empty dots) */
      '#__sp_gate .gd{',
        'display:flex;gap:12px;margin-bottom:20px;height:18px;align-items:center;',
      '}',
      '#__sp_gate .gd span{',
        'width:14px;height:14px;border-radius:50%;',
        'border:2px solid #333;background:transparent;',
        'transition:background 0.1s,border-color 0.1s;',
      '}',
      '#__sp_gate .gd span.filled{background:#00c896;border-color:#00c896}',
      '#__sp_gate .gd span.err-dot{background:#ff4d4d;border-color:#ff4d4d}',

      /* Hidden real input (receives keystrokes) */
      '#__sp_gate input[type=password]{',
        'position:absolute;opacity:0;width:1px;height:1px;pointer-events:none;',
      '}',

      /* Number pad */
      '#__sp_gate .gpad{',
        'display:grid;grid-template-columns:repeat(3,1fr);gap:10px;',
        'width:100%;max-width:260px;margin-bottom:4px;',
      '}',
      '#__sp_gate .gk{',
        'aspect-ratio:1;border:none;border-radius:50%;',
        'background:#1e1e1e;color:#f0f0f0;',
        'font-size:22px;font-weight:600;',
        'cursor:pointer;display:flex;align-items:center;justify-content:center;',
        '-webkit-tap-highlight-color:transparent;',
        'transition:background 0.1s;',
        'user-select:none;-webkit-user-select:none;',
      '}',
      '#__sp_gate .gk:active{background:#2c2c2c}',
      '#__sp_gate .gk.empty{background:transparent;pointer-events:none}',
      '#__sp_gate .gk.del{background:transparent;font-size:20px}',
      '#__sp_gate .gk.del:active{background:#1e1e1e}',

      /* Status / error text */
      '#__sp_gate .gerr{',
        'font-size:13px;color:#ff4d4d;margin-top:14px;min-height:18px;',
        'text-align:center;width:100%;max-width:300px;',
      '}',
      '#__sp_gate .gfoot{',
        'font-size:11px;color:#333;margin-top:24px;',
        'text-align:center;max-width:260px;line-height:1.75;',
      '}',

      /* Shake animation for wrong PIN */
      '@keyframes g-shake{',
        '0%,100%{transform:translateX(0)}',
        '20%{transform:translateX(-10px)}',
        '60%{transform:translateX(10px)}',
      '}',
      '.g-shake{animation:g-shake 0.35s ease}',
    ].join('');
    document.head.appendChild(s);
  }

  // ── Build and show the gate overlay ───────────────────────────────────
  function showGate(onSuccess) {
    injectGateStyles();
    var hasPin   = !!storedHash();
    var isSetup  = !hasPin;
    var phase    = isSetup ? 'set' : 'unlock';  // 'set' | 'confirm' | 'unlock'
    var firstPin = '';

    var gate = document.createElement('div');
    gate.id  = '__sp_gate';

    // ── Render gate HTML ──────────────────────────────────────────────
    function render() {
      var title = isSetup
        ? (phase === 'confirm' ? 'Confirm your PIN' : 'Create your PIN')
        : 'Enter your PIN';

      var subtitle = isSetup
        ? (phase === 'confirm'
            ? 'Re-enter your 6-digit PIN to confirm.'
            : 'Choose a 6-digit PIN. You\'ll use this every<br>time you open StockPicks on this device.')
        : 'Enter your 6-digit PIN to<br>access your picks dashboard.';

      gate.innerHTML = [
        '<div class="gi">📈</div>',
        '<div class="gn">Stock<em>Picks</em></div>',
        '<div class="gs">' + subtitle + '</div>',

        /* Dot display */
        '<div class="gd" id="__sp_dots">',
          '<span></span><span></span><span></span>',
          '<span></span><span></span><span></span>',
        '</div>',

        /* Hidden real input */
        '<input id="__sp_real" type="password" inputmode="numeric" maxlength="6" autocomplete="one-time-code">',

        /* Numpad */
        '<div class="gpad" id="__sp_pad">',
          [1,2,3,4,5,6,7,8,9].map(function(n){
            return '<button class="gk" data-d="'+n+'">'+n+'</button>';
          }).join(''),
          '<button class="gk empty"></button>',
          '<button class="gk" data-d="0">0</button>',
          '<button class="gk del" id="__sp_del">⌫</button>',
        '</div>',

        '<div class="gerr" id="__sp_err"></div>',
        '<div class="gfoot">',
          isSetup
            ? 'PIN is hashed &amp; stored only on this device — never sent anywhere.<br>One login covers all StockPicks apps.'
            : 'One login covers the Dashboard and<br>Watchlist apps at this origin.',
        '</div>',
      ].join('');

      attachEvents();
    }

    // ── Event wiring ──────────────────────────────────────────────────
    function attachEvents() {
      var input = gate.querySelector('#__sp_real');
      var dots  = gate.querySelectorAll('#__sp_dots span');
      var err   = gate.querySelector('#__sp_err');

      function updateDots(val) {
        dots.forEach(function (d, i) {
          d.classList.toggle('filled', i < val.length);
          d.classList.remove('err-dot');
        });
      }

      function showErr(msg) {
        err.textContent = msg;
        dots.forEach(function (d) {
          d.classList.remove('filled');
          d.classList.add('err-dot');
        });
        gate.querySelector('#__sp_dots').classList.add('g-shake');
        setTimeout(function () {
          gate.querySelector('#__sp_dots').classList.remove('g-shake');
          dots.forEach(function (d) { d.classList.remove('err-dot'); });
          input.value = '';
          updateDots('');
        }, 500);
      }

      // Numpad digit buttons
      gate.querySelectorAll('.gk[data-d]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          if (input.value.length >= 6) return;
          input.value += btn.dataset.d;
          updateDots(input.value);
          if (input.value.length === 6) submit(input.value);
        });
      });

      // Delete button
      gate.querySelector('#__sp_del').addEventListener('click', function () {
        input.value = input.value.slice(0, -1);
        updateDots(input.value);
        err.textContent = '';
      });

      // Physical keyboard support
      input.addEventListener('input', function () {
        var v = input.value.replace(/\D/g, '').slice(0, 6);
        input.value = v;
        updateDots(v);
        if (v.length === 6) submit(v);
      });

      // Focus input to capture keyboard on desktop
      setTimeout(function () { input.focus(); }, 60);
    }

    // ── Submit logic ──────────────────────────────────────────────────
    function submit(pin) {
      var err = gate.querySelector('#__sp_err');
      err.textContent = '';

      if (phase === 'unlock') {
        tryUnlock(pin).then(function (ok) {
          if (ok) { dismiss(); }
          else {
            var input = gate.querySelector('#__sp_real');
            input.value = '';
            gate.querySelectorAll('#__sp_dots span').forEach(function(d){
              d.classList.remove('filled');
              d.classList.add('err-dot');
            });
            gate.querySelector('#__sp_dots').classList.add('g-shake');
            setTimeout(function () {
              gate.querySelector('#__sp_dots').classList.remove('g-shake');
              gate.querySelectorAll('#__sp_dots span').forEach(function(d){
                d.classList.remove('err-dot');
              });
            }, 500);
            err.textContent = 'Incorrect PIN — try again';
          }
        });

      } else if (phase === 'set') {
        firstPin = pin;
        phase    = 'confirm';
        render();

      } else if (phase === 'confirm') {
        if (pin !== firstPin) {
          firstPin = '';
          phase    = 'set';
          render();
          setTimeout(function () {
            gate.querySelector('#__sp_err').textContent = 'PINs didn\'t match — start again';
          }, 50);
        } else {
          setupPin(pin, pin).then(function () { dismiss(); });
        }
      }
    }

    function dismiss() {
      gate.style.transition = 'opacity 0.2s ease';
      gate.style.opacity    = '0';
      setTimeout(function () { gate.remove(); onSuccess(); }, 220);
    }

    render();
    document.body.appendChild(gate);
  }

})();
