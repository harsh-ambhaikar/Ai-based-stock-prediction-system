(() => {
  if (localStorage.getItem('stockwise-theme') !== 'light') document.body.classList.add('dark-mode');
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const symbols = ['AAPL', 'NVDA', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META', 'AMD'];
  let activeSymbol = null;
  let marketCache = {};
  const marketPromises = {};
  let chart = null;
  let activeRange = 252;
  let exploreLoaded = false;

  const money = n => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(n);
  const escapeHtml = text => String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  function showToast(message, type = 'error') {
    let stack = $('#toastStack');
    if (!stack) { stack = document.createElement('div'); stack.className = 'toast-stack'; stack.id = 'toastStack'; document.body.append(stack); }
    const toast = document.createElement('div'); toast.className = `toast ${type}`; toast.textContent = message; stack.append(toast);
    setTimeout(() => toast.remove(), 4200);
  }

  async function api(url, options = {}) {
    if (options.method === 'POST') options.headers = { ...(options.headers || {}), 'X-CSRFToken': $('meta[name="csrf-token"]')?.content || '' };
    const response = await fetch(url, options);
    const body = await response.json();
    if (response.status === 202) return body;
    if (!response.ok) throw new Error(body.error || 'Something went wrong. Please try again.');
    return body;
  }

  function requestStock(symbol) {
    symbol = symbol.toUpperCase();
    if (marketCache[symbol]) return Promise.resolve(marketCache[symbol]);
    if (!marketPromises[symbol]) {
      marketPromises[symbol] = (async () => {
        for (let attempt = 0; attempt < 15; attempt++) {
          const stock = await api(`/api/stock/${encodeURIComponent(symbol)}`);
          if (!stock.pending) return (marketCache[symbol] = stock);
          await new Promise(resolve => setTimeout(resolve, 800));
        }
        throw new Error(`Still waiting for verified price history for ${symbol}. Please retry shortly.`);
      })().finally(() => delete marketPromises[symbol]);
    }
    return marketPromises[symbol];
  }

  function drawChart(stock, forecast = null) {
    const canvas = $('#priceChart');
    if (!canvas || !window.Chart) return;
    const history = stock.history;
    const start = Math.max(0, history.close.length - activeRange);
    const labels = history.dates.slice(start);
    const prices = history.close.slice(start);
    let historicalData = prices;
    let forecastData = labels.map(() => null);
    if (forecast) {
      labels.push(...forecast.dates);
      historicalData = [...prices, ...forecast.forecast.map(() => null)];
      forecastData = [...Array(Math.max(0, prices.length - 1)).fill(null), prices.at(-1), ...forecast.forecast];
    }
    const ctx = canvas.getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 280);
    gradient.addColorStop(0, 'rgba(63, 205, 165, .17)'); gradient.addColorStop(1, 'rgba(63, 205, 165, 0)');
    if (chart) chart.destroy();
    chart = new Chart(ctx, { type: 'line', data: { labels, datasets: [
      { label: 'Closing price', data: historicalData, borderColor: '#37bd98', backgroundColor: gradient, borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 4, fill: true, tension: .35 },
      ...(forecast ? [{ label: 'ML outlook', data: forecastData, borderColor: '#8183f6', backgroundColor: 'transparent', borderWidth: 2, borderDash: [6, 5], pointRadius: 0, pointHoverRadius: 4, spanGaps: true, tension: .25 }] : [])
    ] }, options: { responsive: true, maintainAspectRatio: false, interaction: { intersect: false, mode: 'index' }, plugins: {
      legend: { display: false }, tooltip: { backgroundColor: '#11182a', padding: 11, titleFont: { family: 'DM Sans' }, bodyFont: { family: 'DM Sans' }, callbacks: { label: item => `${item.dataset.label}: ${money(item.parsed.y)}` } }
    }, scales: { x: { grid: { display: false }, ticks: { color: '#a0a4b7', maxTicksLimit: 7, maxRotation: 0, font: { family: 'DM Sans', size: 10 } }, border: { display: false } }, y: { position: 'right', grid: { color: 'rgba(100,108,130,.10)' }, ticks: { color: '#a0a4b7', maxTicksLimit: 5, callback: val => '$' + Number(val).toLocaleString('en-US', { maximumFractionDigits: 0 }), font: { family: 'DM Sans', size: 10 } }, border: { display: false } } } } });
  }

  function cardHtml(stock) {
    const up = stock.changePct >= 0;
    const points = stock.history.close.slice(-26);
    const min = Math.min(...points), max = Math.max(...points), width = 140, height = 44;
    const coords = points.map((p, i) => `${(i / (points.length - 1)) * width},${height - ((p - min) / (max - min || 1)) * height}`).join(' ');
    const sourceText = stock.source === 'live+kaggle' ? 'Kaggle + live history' : stock.source === 'snapshot+live' ? 'Historical + live data' : stock.source === 'snapshot' ? `Daily dataset · ${stock.lastDate}` : stock.source === 'live' ? 'Live history' : stock.source === 'kaggle' ? `Kaggle · through ${stock.lastDate}` : 'Illustrative sample';
    return `<button class="stock-card" data-symbol="${escapeHtml(stock.symbol)}"><div class="stock-card-head"><span class="ticker-icon ${stock.symbol.toLowerCase()}">${escapeHtml(stock.symbol.slice(0, 1))}</span><span class="card-arrow">↗</span></div><div class="stock-card-name"><b>${escapeHtml(stock.symbol)}</b><span>${escapeHtml(stock.company)}</span><small class="source-label">${escapeHtml(sourceText)}</small></div><div class="stock-spark"><svg viewBox="0 0 140 44" preserveAspectRatio="none"><polyline points="${coords}" fill="none" stroke="${up ? '#36bc98' : '#f07178'}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></div><div class="stock-card-price"><b>${money(stock.price)}</b><span class="${up ? 'positive' : 'negative'}">${up ? '+' : ''}${stock.changePct.toFixed(2)}%</span></div></button>`;
  }

  async function loadStock(symbol) {
    symbol = symbol.trim().toUpperCase();
    if (!symbol) return;
    activeSymbol = symbol;
    $('#stockSearch') && ($('#stockSearch').value = symbol);
    $('#exploreSearch') && ($('#exploreSearch').value = symbol);
    try {
      const stock = await requestStock(symbol);
      $('#chartSymbol').textContent = stock.symbol;
      $('#chartCompany').textContent = stock.company;
      $('#chartPrice').textContent = money(stock.price);
      const isSnapshot = stock.source === 'kaggle' || stock.source === 'snapshot';
      const change = isSnapshot ? `${stock.lastDate} close · ${stock.source === 'kaggle' ? 'Kaggle history' : 'daily market data'}` : `${stock.change >= 0 ? '+' : ''}${money(stock.change)} (${stock.changePct >= 0 ? '+' : ''}${stock.changePct.toFixed(2)}%) today`;
      $('#chartChange').textContent = change;
      $('#chartChange').className = isSnapshot ? '' : stock.changePct >= 0 ? 'positive' : 'negative';
      $('#analysisSubtitle').textContent = `${stock.company} · ${stock.symbol}`;
      const sourceCaption = $('.chart-caption'); if (sourceCaption) sourceCaption.textContent = `Daily closing prices · USD · ${stock.source === 'live+kaggle' ? 'Kaggle history + live prices' : stock.source === 'snapshot+live' ? 'historical dataset + live prices' : stock.source === 'live' ? 'market data' : stock.source === 'snapshot' ? `daily dataset through ${stock.lastDate}` : stock.source === 'kaggle' ? `Kaggle snapshot through ${stock.lastDate}` : 'illustrative sample'}`;
      $('#high52').textContent = money(stock.stats.high52); $('#low52').textContent = money(stock.stats.low52);
      $('#sma20').textContent = money(stock.stats.sma20); $('#sma50').textContent = money(stock.stats.sma50);
      $('#emptyChart')?.classList.add('hidden');
      $('#predictButton').disabled = false;
      updateSaveButton();
      $('#forecastResult').innerHTML = '<div class="forecast-placeholder"><span>✳</span><div><b>Your outlook will appear here</b><small>Generate a forecast to explore the model estimate.</small></div></div>';
      drawChart(stock);
      refreshLiveQuote();
      $('#analysisSubtitle')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) { showToast(err.message); }
  }

  function setFeedStatus(message, state = '') {
    const badge = $('#marketFeedStatus');
    if (!badge) return;
    badge.classList.toggle('feed-live', state === 'live');
    badge.classList.toggle('feed-warning', state === 'warning');
    const text = badge.querySelector('span');
    if (text) text.textContent = message;
  }

  async function refreshLiveQuote() {
    if (!activeSymbol || document.visibilityState !== 'visible') return;
    const symbol = activeSymbol;
    try {
      const quote = await api(`/api/quote/${encodeURIComponent(symbol)}`);
      if (symbol !== activeSymbol) return;
      if (!quote.configured) {
        setFeedStatus('Market snapshot');
        return;
      }
      if (!quote.available) {
        setFeedStatus('Live quote unavailable · showing daily data', 'warning');
        return;
      }
      const price = Number(quote.price);
      const change = Number(quote.change || 0);
      const changePct = Number(quote.changePct || 0);
      $('#chartPrice').textContent = money(price);
      const changeEl = $('#chartChange');
      if (changeEl) {
        changeEl.textContent = `${change >= 0 ? '+' : ''}${money(change)} (${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%) today`;
        changeEl.className = changePct >= 0 ? 'positive' : 'negative';
      }
      const receivedAt = quote.timestamp ? new Date(quote.timestamp * 1000) : new Date();
      const recent = quote.timestamp && (Date.now() / 1000 - quote.timestamp) < 90;
      setFeedStatus(`${recent ? 'Live' : 'Last quote'} · ${receivedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`, recent ? 'live' : '');
      const caption = $('.chart-caption');
      if (caption) caption.textContent = `Daily history + latest quote · USD · ${quote.provider}`;
      if (caption) caption.textContent = `Daily history + latest quote · USD · ${quote.provider}`;
      const card = $(`#stockGrid [data-symbol="${CSS.escape(symbol)}"]`);
      const cardPrice = card?.querySelector('.stock-card-price b');
      const cardChange = card?.querySelector('.stock-card-price span');
      if (cardPrice) cardPrice.textContent = money(price);
      if (cardChange) {
        cardChange.textContent = `${changePct >= 0 ? '+' : ''}${changePct.toFixed(2)}%`;
        cardChange.className = changePct >= 0 ? 'positive' : 'negative';
      }
      if (chart && chart.data.datasets[0]) {
        const label = receivedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const labels = chart.data.labels;
        if (labels[labels.length - 1] === label) chart.data.datasets[0].data[chart.data.datasets[0].data.length - 1] = price;
        else {
          labels.push(label);
          chart.data.datasets[0].data.push(price);
          if (chart.data.datasets[1]) chart.data.datasets[1].data.push(null);
        }
        chart.update('none');
      }
    } catch {
      setFeedStatus('Live quote connection unavailable', 'warning');
    }
  }

  function renderHistory(rows, target) {
    if (!target) return;
    if (!rows.length) { target.innerHTML = '<tr><td colspan="6" class="table-empty">No forecasts yet. Choose a stock and generate your first outlook.</td></tr>'; return; }
    target.innerHTML = rows.map(row => `<tr><td><span class="history-stock"><span class="ticker-icon ${escapeHtml(row.symbol.toLowerCase())}">${escapeHtml(row.symbol.slice(0, 1))}</span><span><b>${escapeHtml(row.symbol)}</b><small>${escapeHtml(row.company)}</small></span></span></td><td>${escapeHtml(row.created)}</td><td>${escapeHtml(row.horizonLabel || `${row.horizon} trading days`)}</td><td>${money(row.current)}</td><td><b>${money(row.predicted)}</b></td><td><span class="move-badge ${row.change >= 0 ? 'up' : 'down'}">${row.change >= 0 ? '+' : ''}${row.change.toFixed(2)}%</span></td></tr>`).join('');
  }

  async function refreshHistory() {
    try { const rows = await api('/api/history'); renderHistory(rows.slice(0, 5), $('#recentHistory')); renderHistory(rows, $('#allHistory')); }
    catch (err) { if ($('#allHistory')) $('#allHistory').innerHTML = `<tr><td colspan="6" class="table-empty">${escapeHtml(err.message)}</td></tr>`; }
  }

  async function makePrediction() {
    if (!activeSymbol) return;
    const button = $('#predictButton');
    button.disabled = true; button.innerHTML = '<span class="button-spinner"></span> Reading patterns…';
    try {
      const forecast = await api('/api/predict', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: activeSymbol, amount: $('#horizonAmount').value, unit: $('#horizonUnit').value }) });
      const up = forecast.change >= 0;
      const originText = ['kaggle', 'snapshot'].includes(forecast.source) ? `from ${money(forecast.current)} · ${forecast.lastDate} close` : `from ${money(forecast.current)} today`;
      $('#forecastResult').innerHTML = `<div class="forecast-outcome"><div class="forecast-outcome-main"><span class="outlook-icon">✳</span><div><small>${escapeHtml(forecast.horizonLabel)} estimate</small><b>${money(forecast.predicted)}</b><span>${escapeHtml(originText)}</span></div></div><div class="forecast-outcome-move ${up ? 'positive' : 'negative'}"><span>Estimated model move</span><b>${up ? '+' : ''}${forecast.change.toFixed(2)}%</b></div><div class="model-tag"><span class="model-dot"></span>${escapeHtml(forecast.model)}<small class="model-validation">Holdout MAE ${forecast.validationMaePct.toFixed(2)}%</small></div></div>`;
      drawChart(marketCache[activeSymbol], forecast); refreshHistory();
    } catch (err) { showToast(err.message); }
    finally { button.disabled = !activeSymbol; button.innerHTML = 'Generate outlook <span>✳</span>'; }
  }

  function wireSearch(inputId, buttonId) {
    const input = $('#' + inputId), button = $('#' + buttonId), suggestions = $('#searchSuggestions');
    if (!input) return;
    const submit = () => { const value = input.value.trim().toUpperCase(); if (suggestions) suggestions.classList.remove('open'); if (value) { loadStock(value); showView('overview'); } };
    button?.addEventListener('click', submit);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); if (e.key === 'Escape') suggestions?.classList.remove('open'); });
    input.addEventListener('input', () => {
      if (!suggestions || inputId !== 'stockSearch') return;
      const q = input.value.trim().toUpperCase();
      const matches = symbols.filter(s => s.includes(q)).slice(0, 5);
      if (q && matches.length) { suggestions.innerHTML = matches.map(s => `<button data-symbol="${s}"><span class="ticker-icon ${s.toLowerCase()}">${s[0]}</span><span><b>${s}</b><small>${escapeHtml(marketCache[s]?.company || '')}</small></span><span>↗</span></button>`).join(''); suggestions.classList.add('open'); }
      else suggestions.classList.remove('open');
    });
  }

  function showView(name) {
    ['overview', 'history', 'explore'].forEach(v => { $('#' + v + 'View')?.classList.toggle('hidden', v !== name); $(`[data-view="${v}"]`)?.classList.toggle('active', v === name); });
    $('#breadcrumbTitle').textContent = name === 'overview' ? 'Overview' : name === 'history' ? 'Prediction history' : 'Explore stocks';
    if (name === 'history') refreshHistory();
    if (name === 'explore' && !exploreLoaded) { exploreLoaded = true; populateGrid('exploreGrid'); }
  }

  async function populateGrid(id) {
    const grid = $('#' + id); if (!grid) return;
    const list = ['AAPL', 'NVDA', 'MSFT', 'GOOGL'];
    try {
      const stocks = await Promise.all(list.map(requestStock));
      grid.innerHTML = stocks.map(cardHtml).join('');
      if (!activeSymbol) loadStock('AAPL');
    } catch { grid.innerHTML = '<div class="table-empty">Market data is unavailable right now. Search to try again.</div>'; }
  }

  function watchlistKey() { return `stockwise-watchlist-${$('meta[name="stockwise-user"]')?.content || 'guest'}`; }
  function getWatchlist() { try { return JSON.parse(localStorage.getItem(watchlistKey()) || '[]'); } catch { return []; } }
  function renderWatchlist() {
    const list = $('#savedStocks'); if (!list) return;
    const saved = getWatchlist();
    list.innerHTML = saved.length ? saved.map(s => `<button data-symbol="${escapeHtml(s)}"><span class="ticker-icon ${escapeHtml(s.toLowerCase())}">${escapeHtml(s[0])}</span><span><b>${escapeHtml(s)}</b><small>Saved stock</small></span><span class="watch-remove" data-remove="${escapeHtml(s)}" title="Remove">×</span></button>`).join('') : '<div class="watch-empty">Save a stock to keep it close.</div>';
    updateSaveButton();
  }
  function updateSaveButton() {
    const button = $('#saveStockButton'); if (!button || !activeSymbol) return;
    const saved = getWatchlist().includes(activeSymbol);
    button.classList.toggle('saved', saved);
    button.innerHTML = saved ? '★ Saved' : '☆ Save stock';
  }
  function setupEnhancements() {
    const actions = $('.top-actions') || $('.nav-actions');
    const topbar = $('.topbar');
    if (!$('#themeToggle')) {
      const toggle = document.createElement('button'); toggle.id = 'themeToggle'; toggle.className = 'theme-toggle'; toggle.type = 'button'; toggle.title = 'Toggle dark mode';
      if (actions) actions.prepend(toggle); else if (topbar) topbar.append(toggle); else { toggle.classList.add('theme-toggle-floating'); document.body.append(toggle); }
      const applyTheme = dark => { document.body.classList.toggle('dark-mode', dark); toggle.textContent = dark ? '☀' : '☾'; toggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode'); };
      applyTheme(localStorage.getItem('stockwise-theme') !== 'light');
      toggle.addEventListener('click', () => { const dark = !document.body.classList.contains('dark-mode'); localStorage.setItem('stockwise-theme', dark ? 'dark' : 'light'); applyTheme(dark); });
    }
    const unitSelect = $('#horizonSelect');
    if (unitSelect) {
      unitSelect.id = 'horizonUnit';
      unitSelect.innerHTML = '<option value="days">Days</option><option value="months">Months</option><option value="years">Years</option>';
      const amount = document.createElement('input'); amount.id = 'horizonAmount'; amount.type = 'number'; amount.min = '1'; amount.max = '1260'; amount.value = '7'; amount.setAttribute('aria-label', 'Forecast amount');
      unitSelect.parentElement.insertBefore(amount, unitSelect);
      const label = unitSelect.parentElement.querySelector('label'); if (label) label.htmlFor = 'horizonAmount';
      unitSelect.addEventListener('change', () => { amount.max = unitSelect.value === 'years' ? '5' : unitSelect.value === 'months' ? '60' : '1260'; amount.value = Math.min(Number(amount.value), Number(amount.max)); });
    }
    const chartTitle = $('.chart-title');
    if (chartTitle && !$('#saveStockButton')) {
      const button = document.createElement('button'); button.id = 'saveStockButton'; button.className = 'save-stock-button'; button.type = 'button'; button.textContent = '☆ Save stock';
      chartTitle.append(button);
      button.addEventListener('click', () => {
        if (!activeSymbol) return;
        const saved = getWatchlist();
        if (saved.includes(activeSymbol)) localStorage.setItem(watchlistKey(), JSON.stringify(saved.filter(s => s !== activeSymbol)));
        else localStorage.setItem(watchlistKey(), JSON.stringify([activeSymbol, ...saved].slice(0, 12)));
        renderWatchlist();
        showToast(getWatchlist().includes(activeSymbol) ? `${activeSymbol} saved to your watchlist.` : `${activeSymbol} removed from your watchlist.`, 'success');
      });
    }
    const sidebar = $('.sidebar');
    if (sidebar && !$('#savedStocks')) {
      const label = document.createElement('div'); label.className = 'side-label saved-label'; label.textContent = 'MY WATCHLIST';
      const list = document.createElement('div'); list.className = 'side-watch saved-watch'; list.id = 'savedStocks';
      sidebar.insertBefore(label, $('.side-bottom')); sidebar.insertBefore(list, $('.side-bottom'));
      list.addEventListener('click', e => {
        const remove = e.target.closest('[data-remove]');
        if (remove) { e.stopPropagation(); const symbol = remove.dataset.remove; localStorage.setItem(watchlistKey(), JSON.stringify(getWatchlist().filter(s => s !== symbol))); renderWatchlist(); return; }
        const item = e.target.closest('[data-symbol]'); if (item) { showView('overview'); loadStock(item.dataset.symbol); }
      });
      renderWatchlist();
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $$('form[method="post"]').forEach(form => {
      if (!form.querySelector('[name="csrf_token"]')) {
        const token = document.createElement('input'); token.type = 'hidden'; token.name = 'csrf_token'; token.value = $('meta[name="csrf-token"]')?.content || ''; form.append(token);
      }
    });
    setupEnhancements();
    setFeedStatus('Connecting to live quote feed');
    setInterval(refreshLiveQuote, 15000);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refreshLiveQuote(); });
    const date = $('#todayDate'); if (date) date.textContent = new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date());
    wireSearch('stockSearch', 'searchButton'); wireSearch('exploreSearch', 'exploreSearchButton');
    $('#quickStocks')?.addEventListener('click', e => { const button = e.target.closest('[data-symbol]'); if (button) { showView('overview'); loadStock(button.dataset.symbol); } });
    $('#stockGrid')?.addEventListener('click', e => { const button = e.target.closest('[data-symbol]'); if (button) loadStock(button.dataset.symbol); });
    $('#exploreGrid')?.addEventListener('click', e => { const button = e.target.closest('[data-symbol]'); if (button) { loadStock(button.dataset.symbol); showView('overview'); } });
    $('#searchSuggestions')?.addEventListener('click', e => { const button = e.target.closest('[data-symbol]'); if (button) loadStock(button.dataset.symbol); $('#searchSuggestions')?.classList.remove('open'); });
    $$('.side-nav [data-view], [data-view]:not(.side-nav a)').forEach(link => link.addEventListener('click', e => { e.preventDefault(); showView(link.dataset.view); }));
    $$('.range-tabs button').forEach(button => button.addEventListener('click', () => { $$('.range-tabs button').forEach(b => b.classList.remove('active')); button.classList.add('active'); activeRange = button.dataset.range === '3mo' ? 63 : button.dataset.range === '6mo' ? 126 : 252; if (activeSymbol && marketCache[activeSymbol]) drawChart(marketCache[activeSymbol]); }));
    $('#predictButton')?.addEventListener('click', makePrediction);
    if ($('#stockGrid')) { populateGrid('stockGrid'); refreshHistory(); }
    const initialView = location.hash.slice(1); if (['history', 'explore'].includes(initialView)) showView(initialView);
    setTimeout(() => $$('.toast').forEach(t => t.remove()), 5000);
  });
})();
