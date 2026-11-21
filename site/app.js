// 大安借車預報：讀 data 分支上的預測檔，畫地圖和清單。
//
// 資料從哪來：GitHub Actions 每 5 分鐘收集完資料就跑 scripts/predict.py，
// 把預測寫進 data 分支的 predictions/ 資料夾。這個網頁只負責讀那兩個檔：
//   stations.json  站名、行政區、經緯度（很少變）
//   latest.json    每站現在幾台、30 分鐘後借不到的機率（每 5 分鐘更新）
// 在自己電腦上開發時（localhost），改讀 site/dev-data/ 裡的範例檔。
(function () {
  "use strict";

  var LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
  var DATA_BASE = LOCAL
    ? "dev-data/"
    : "https://raw.githubusercontent.com/Bryant0909/youbike-forecast/data/predictions/";
  var REFRESH_MS = 150 * 1000;     // 2.5 分鐘重新讀一次（GitHub 那邊的快取是 5 分鐘）
  var STALE_WARN_MIN = 12;         // 超過這麼久沒更新：狀態變黃
  var STALE_BAD_MIN = 20;          // 超過這麼久：顯示警告橫幅
  var MID_PROB = 0.2;              // 「可能借不到」的下限（警告門檻以下、這個以上）

  var state = { stations: [], data: null, area: "all", sort: "risk", q: "", pos: null, active: null };
  var map = null, markers = {}, fitted = false;

  var $ = function (id) { return document.getElementById(id); };
  var pct = function (p) { return Math.round(p * 100) + "%"; };

  function riskOf(prob, thr) {
    if (prob >= thr) return "high";
    if (prob >= MID_PROB) return "mid";
    return "low";
  }
  var RISK_LABEL = { high: "很可能借不到", mid: "可能借不到", low: "應該借得到" };

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function distanceKm(a, b) {
    var R = 6371, toRad = Math.PI / 180;
    var dLat = (b.lat - a.lat) * toRad, dLon = (b.lon - a.lon) * toRad;
    var s = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(a.lat * toRad) * Math.cos(b.lat * toRad) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  // ---------- 讀資料 ----------
  function getJSON(name) {
    return fetch(DATA_BASE + name).then(function (r) {
      if (!r.ok) throw new Error(name + " " + r.status);
      return r.json();
    });
  }

  function load() {
    return Promise.all([getJSON("stations.json"), getJSON("latest.json")]).then(function (res) {
      var meta = {};
      res[0].forEach(function (m) { meta[m.id] = m; });
      var latest = res[1];
      var thr = latest.model.alert_threshold;
      state.data = latest;
      state.stations = latest.stations.filter(function (s) { return meta[s.id]; }).map(function (s) {
        var m = meta[s.id];
        return {
          id: s.id, name: m.name, district: m.district, lat: m.lat, lon: m.lon,
          bikes: s.bikes, docks: s.docks, before: s.bikes_30m_ago, prob: s.prob,
          risk: riskOf(s.prob, thr)
        };
      });
      render();
    }).catch(function (err) {
      console.error(err);
      setStatus("bad", "讀不到預測資料，稍後會自動重試");
    });
  }

  // ---------- 狀態列 ----------
  function setStatus(level, text) {
    $("status-dot").className = "dot " + level;
    $("status-text").textContent = text;
  }

  function renderStatus() {
    if (!state.data) return;
    var t = new Date(state.data.data_time);
    var ageMin = Math.max(0, Math.round((Date.now() - t.getTime()) / 60000));
    var hhmm = t.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Taipei" });
    var level = ageMin >= STALE_BAD_MIN ? "bad" : ageMin >= STALE_WARN_MIN ? "warn" : "ok";
    setStatus(level, "資料時間 " + hhmm + "（" + (ageMin <= 1 ? "剛剛" : ageMin + " 分鐘前") + "）");
    $("stale").hidden = ageMin < STALE_BAD_MIN;
    $("stale-min").textContent = String(ageMin);
  }

  // ---------- 摘要與說明 ----------
  function renderSummary() {
    var n = { high: 0, mid: 0, low: 0 };
    state.stations.forEach(function (s) { n[s.risk] += 1; });
    $("n-high").textContent = n.high;
    $("n-mid").textContent = n.mid;
    $("n-low").textContent = n.low;
    var m = state.data.model;
    $("m-prec").textContent = pct(m.alert_precision);
    $("m-recall").textContent = pct(m.valid_recall_p70);
    $("m-valid").textContent = m.valid_days ? m.valid_days[0] + " ~ " + m.valid_days[1] : "最近幾天";
    $("m-version").textContent = m.version;
  }

  // ---------- 地圖 ----------
  function initMap() {
    if (map || typeof L === "undefined") return;
    map = L.map("map", { zoomControl: true, scrollWheelZoom: true }).setView([25.026, 121.54], 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    }).addTo(map);
  }

  function popupNode(s) {
    var div = document.createElement("div");
    var name = document.createElement("div");
    name.className = "pop-name"; name.textContent = s.name;
    var r1 = document.createElement("div");
    r1.className = "pop-row";
    r1.textContent = "現在 " + s.bikes + " 台" + (s.before == null ? "" : "（30 分鐘前 " + s.before + " 台）");
    var r2 = document.createElement("div");
    r2.className = "pop-row";
    r2.textContent = "30 分鐘後借不到的機率 " + pct(s.prob);
    var r3 = document.createElement("b");
    r3.style.color = cssVar("--" + s.risk);
    r3.textContent = RISK_LABEL[s.risk];
    div.append(name, r1, r2, r3);
    return div;
  }

  function renderMap() {
    initMap();
    if (!map) return;
    var color = { high: cssVar("--high"), mid: cssVar("--mid"), low: cssVar("--low") };
    var seen = {};
    state.stations.forEach(function (s) {
      seen[s.id] = true;
      var style = { radius: s.risk === "high" ? 8 : 6.5, color: "#fff", weight: 1.5, fillColor: color[s.risk], fillOpacity: 0.92 };
      var mk = markers[s.id];
      if (!mk) {
        mk = L.circleMarker([s.lat, s.lon], style).addTo(map);
        mk.on("click", function () { setActive(s.id, false); });
        markers[s.id] = mk;
      } else {
        mk.setStyle(style);
      }
      mk.bindPopup(popupNode(s));
      if (s.risk === "high") mk.bringToFront();
    });
    Object.keys(markers).forEach(function (id) {
      if (!seen[id]) { map.removeLayer(markers[id]); delete markers[id]; }
    });
    if (!fitted && state.stations.length) {
      map.fitBounds(state.stations.map(function (s) { return [s.lat, s.lon]; }), { padding: [20, 20] });
      fitted = true;
    }
  }

  // ---------- 清單 ----------
  function visibleStations() {
    var q = state.q.trim();
    var list = state.stations.filter(function (s) {
      return (state.area === "all" || s.district === state.area) && (!q || s.name.indexOf(q) !== -1);
    });
    if (state.sort === "name") {
      list.sort(function (a, b) { return a.name.localeCompare(b.name, "zh-Hant"); });
    } else if (state.sort === "near" && state.pos) {
      list.forEach(function (s) { s.km = distanceKm(state.pos, s); });
      list.sort(function (a, b) { return a.km - b.km; });
    } else {
      list.sort(function (a, b) { return b.prob - a.prob; });
    }
    return list;
  }

  function stationItem(s) {
    var li = document.createElement("li");
    li.className = "st" + (s.id === state.active ? " is-active" : "");
    li.tabIndex = 0;
    li.dataset.id = s.id;

    var name = document.createElement("div");
    name.className = "st-name"; name.textContent = s.name;

    var sub = document.createElement("div");
    sub.className = "st-sub";
    var parts = [s.district === "臺大公館校區" ? "臺大校區" : s.district, "現在 " + s.bikes + " 台"];
    if (s.before != null && s.before !== s.bikes) {
      parts.push((s.bikes > s.before ? "比 30 分鐘前多 " : "比 30 分鐘前少 ") + Math.abs(s.bikes - s.before) + " 台");
    }
    if (state.sort === "near" && s.km != null) parts.push((s.km < 1 ? Math.round(s.km * 1000) + " 公尺" : s.km.toFixed(1) + " 公里"));
    sub.textContent = parts.join(" · ");

    var risk = document.createElement("div");
    risk.className = "st-risk";
    var badge = document.createElement("span");
    badge.className = "badge risk-" + s.risk; badge.textContent = RISK_LABEL[s.risk];
    var bar = document.createElement("span");
    bar.className = "bar";
    var fill = document.createElement("i");
    fill.className = "risk-" + s.risk; fill.style.width = Math.max(2, Math.round(s.prob * 100)) + "%";
    bar.appendChild(fill);
    var prob = document.createElement("span");
    prob.className = "prob"; prob.textContent = "借不到 " + pct(s.prob);
    risk.append(badge, bar, prob);

    li.append(name, risk, sub);
    li.addEventListener("click", function () { setActive(s.id, true); });
    li.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActive(s.id, true); } });
    return li;
  }

  function renderList() {
    var list = visibleStations();
    var ol = $("stations");
    ol.replaceChildren();
    if (!list.length) {
      var empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = state.stations.length ? "找不到符合的站" : "還沒有資料";
      ol.appendChild(empty);
    } else {
      list.forEach(function (s) { ol.appendChild(stationItem(s)); });
    }
    $("list-meta").textContent = "共 " + list.length + " 站" + (state.q ? "（搜尋「" + state.q + "」）" : "");
  }

  function setActive(id, fromList) {
    state.active = id;
    document.querySelectorAll(".st").forEach(function (el) { el.classList.toggle("is-active", el.dataset.id === id); });
    var mk = markers[id];
    if (mk && map) {
      if (fromList) map.setView(mk.getLatLng(), Math.max(map.getZoom(), 16));
      mk.openPopup();
    }
    if (!fromList) {
      var el = document.querySelector('.st[data-id="' + id + '"]');
      if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }

  function render() {
    renderStatus();
    renderSummary();
    renderMap();
    renderList();
  }

  // ---------- 控制項 ----------
  document.querySelectorAll("[data-area]").forEach(function (b) {
    b.addEventListener("click", function () {
      state.area = b.dataset.area;
      document.querySelectorAll("[data-area]").forEach(function (o) { o.setAttribute("aria-pressed", String(o === b)); });
      renderList();
    });
  });

  function setSort(sort) {
    state.sort = sort;
    document.querySelectorAll("[data-sort]").forEach(function (o) { o.setAttribute("aria-pressed", String(o.dataset.sort === sort)); });
    renderList();
  }
  document.querySelectorAll("[data-sort]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (b.dataset.sort !== "near") { setSort(b.dataset.sort); return; }
      if (!navigator.geolocation) { $("list-meta").textContent = "這個瀏覽器不能取得位置"; return; }
      $("list-meta").textContent = "取得位置中…";
      navigator.geolocation.getCurrentPosition(function (p) {
        state.pos = { lat: p.coords.latitude, lon: p.coords.longitude };
        setSort("near");
      }, function () {
        $("list-meta").textContent = "沒有取得位置（可能是沒有允許），改用風險排序";
        setSort("risk");
      }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
    });
  });

  $("q").addEventListener("input", function (e) { state.q = e.target.value; renderList(); });

  load();
  setInterval(load, REFRESH_MS);
  setInterval(renderStatus, 30 * 1000);   // 「幾分鐘前」每 30 秒更新一次
})();
