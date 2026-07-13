(() => {
  "use strict";

  const PAGE_SIZE = 40;
  const COLORS = {
    below: "#bc4a3a",
    near: "#c18a25",
    above: "#117a68",
    empty: "#a9afa8",
    ink: "#17211d",
    grid: "#d6d8d0",
  };

  const state = {
    data: null,
    records: [],
    filtered: [],
    selectedProvince: "all",
    propertyType: "all",
    relation: "all",
    query: "",
    sort: "ratio_asc",
    page: 1,
    map: null,
    markerLayer: null,
    mapLayers: null,
    activeMapStyle: "street",
    mapStyleButtons: [],
    markerByProvince: new Map(),
    chart: null,
  };

  const elements = {};

  const compactCurrency = new Intl.NumberFormat("tr-TR", {
    style: "currency",
    currency: "TRY",
    notation: "compact",
    maximumFractionDigits: 1,
  });

  const fullCurrency = new Intl.NumberFormat("tr-TR", {
    style: "currency",
    currency: "TRY",
    maximumFractionDigits: 0,
  });

  const integerFormat = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

  function cacheElements() {
    [
      "headerStatus", "recordMetric", "recordDetail", "provinceMetric", "appraisedMetric",
      "auctionMetric", "ratioMetric", "searchInput", "provinceSelect", "propertySelect",
      "resetButton", "exportButton", "mapSelection", "inspectorTitle", "inspectorCount",
      "inspectorAppraised", "inspectorAuction", "inspectorRatio", "inspectorBelow",
      "typeBreakdown", "distributionBars", "recordsBody", "emptyState", "tableCount",
      "sortSelect", "previousPage", "nextPage", "pageLabel", "generatedAt", "loadingLayer",
    ].forEach((id) => { elements[id] = document.getElementById(id); });
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  }

  function relationFor(ratio) {
    if (ratio < 0.95) return "below";
    if (ratio <= 1.05) return "near";
    return "above";
  }

  function relationLabel(ratio) {
    const relation = relationFor(ratio);
    return relation === "below" ? "Muhammen altı" : relation === "near" ? "Muhammene yakın" : "Muhammen üstü";
  }

  function formatRatio(ratio) {
    return ratio == null ? "—" : `%${(ratio * 100).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;
  }

  function formatCurrency(value, compact = false) {
    if (value == null || Number.isNaN(value)) return "—";
    return (compact ? compactCurrency : fullCurrency).format(value);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function markerColor(ratio, count) {
    if (!count || ratio == null) return COLORS.empty;
    return COLORS[relationFor(ratio)];
  }

  function populateControls() {
    state.data.provinces.forEach((province) => {
      const option = document.createElement("option");
      option.value = province.name;
      option.textContent = province.count ? `${province.name} (${integerFormat.format(province.count)})` : `${province.name} (kayıt yok)`;
      elements.provinceSelect.appendChild(option);
    });

    const types = [...new Set(state.records.map((record) => record.property_type))]
      .sort((a, b) => a.localeCompare(b, "tr"));
    types.forEach((type) => {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = type[0].toLocaleUpperCase("tr-TR") + type.slice(1);
      elements.propertySelect.appendChild(option);
    });
  }

  function bindControls() {
    let searchTimer;
    elements.searchInput.addEventListener("input", (event) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => {
        state.query = event.target.value.trim().toLocaleLowerCase("tr-TR");
        state.page = 1;
        applyFilters();
      }, 140);
    });

    elements.provinceSelect.addEventListener("change", (event) => {
      state.selectedProvince = event.target.value;
      state.page = 1;
      applyFilters();
      focusSelectedProvince();
    });

    elements.propertySelect.addEventListener("change", (event) => {
      state.propertyType = event.target.value;
      state.page = 1;
      applyFilters();
    });

    document.getElementById("relationControl").addEventListener("change", (event) => {
      if (event.target.name !== "relation") return;
      state.relation = event.target.value;
      state.page = 1;
      applyFilters();
    });

    elements.sortSelect.addEventListener("change", (event) => {
      state.sort = event.target.value;
      state.page = 1;
      renderTable();
    });

    elements.resetButton.addEventListener("click", resetFilters);
    elements.exportButton.addEventListener("click", exportCsv);
    elements.previousPage.addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        renderTable();
        elements.recordsBody.closest(".records-section").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
    elements.nextPage.addEventListener("click", () => {
      const pageCount = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
      if (state.page < pageCount) {
        state.page += 1;
        renderTable();
        elements.recordsBody.closest(".records-section").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  }

  function resetFilters() {
    state.selectedProvince = "all";
    state.propertyType = "all";
    state.relation = "all";
    state.query = "";
    state.page = 1;
    elements.searchInput.value = "";
    elements.provinceSelect.value = "all";
    elements.propertySelect.value = "all";
    document.querySelector('input[name="relation"][value="all"]').checked = true;
    applyFilters();
    if (state.map) state.map.flyTo([39.0, 35.2], 6, { duration: 0.6 });
  }

  function applyFilters() {
    state.filtered = state.records.filter((record) => {
      if (state.selectedProvince !== "all" && record.province !== state.selectedProvince) return false;
      if (state.propertyType !== "all" && record.property_type !== state.propertyType) return false;
      if (state.relation !== "all" && relationFor(record.ratio) !== state.relation) return false;
      if (state.query) {
        const haystack = `${record.record_ref} ${record.province} ${record.property_type}`.toLocaleLowerCase("tr-TR");
        if (!haystack.includes(state.query)) return false;
      }
      return true;
    });
    state.page = Math.min(state.page, Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE)));
    renderAll();
  }

  function renderAll() {
    renderMetrics();
    renderMap();
    renderInspector();
    renderChart();
    renderDistribution();
    renderTable();
  }

  function renderMetrics() {
    const records = state.filtered;
    const represented = new Set(records.map((record) => record.province)).size;
    elements.recordMetric.textContent = integerFormat.format(records.length);
    elements.recordDetail.textContent = `toplam ${integerFormat.format(state.records.length)} satış`;
    elements.provinceMetric.textContent = `${represented}/81`;
    elements.appraisedMetric.textContent = formatCurrency(median(records.map((record) => record.appraised_value)), true);
    elements.auctionMetric.textContent = formatCurrency(median(records.map((record) => record.auction_price)), true);
    elements.ratioMetric.textContent = formatRatio(median(records.map((record) => record.ratio)));
  }

  function initializeMap() {
    if (!window.L) {
      document.getElementById("map").innerHTML = '<div class="empty-state">Harita kütüphanesi yüklenemedi.</div>';
      return;
    }
    state.map = L.map("map", {
      zoomControl: true,
      minZoom: 5,
      maxZoom: 18,
      scrollWheelZoom: false,
      zoomSnap: 0.5,
    }).setView([39.0, 35.2], 6);

    state.mapLayers = {
      street: L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
        subdomains: "abcd",
        maxZoom: 20,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      }),
      satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri",
      }),
    };
    state.mapLayers.street.addTo(state.map);
    state.markerLayer = L.layerGroup().addTo(state.map);
    L.control.scale({ imperial: false, position: "bottomright", maxWidth: 110 }).addTo(state.map);

    const styleControl = L.control({ position: "topright" });
    styleControl.onAdd = () => {
      const container = L.DomUtil.create("div", "leaflet-control map-style-control");
      container.setAttribute("role", "group");
      container.setAttribute("aria-label", "Harita görünümü");
      container.innerHTML = '<button type="button" data-map-style="street" class="active" aria-pressed="true">Harita</button><button type="button" data-map-style="satellite" aria-pressed="false">Uydu</button>';
      L.DomEvent.disableClickPropagation(container);
      L.DomEvent.disableScrollPropagation(container);
      state.mapStyleButtons = [...container.querySelectorAll("button")];
      state.mapStyleButtons.forEach((button) => {
        button.addEventListener("click", () => setMapStyle(button.dataset.mapStyle));
      });
      return container;
    };
    styleControl.addTo(state.map);
  }

  function setMapStyle(style) {
    if (!state.map || !state.mapLayers?.[style] || state.activeMapStyle === style) return;
    Object.values(state.mapLayers).forEach((layer) => {
      if (state.map.hasLayer(layer)) state.map.removeLayer(layer);
    });
    state.mapLayers[style].addTo(state.map);
    state.activeMapStyle = style;
    state.mapStyleButtons.forEach((button) => {
      const active = button.dataset.mapStyle === style;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  function markerCount(count) {
    if (count < 1000) return integerFormat.format(count);
    return `${(count / 1000).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}B`;
  }

  function provinceMarkerIcon(count, relation, selected) {
    const size = count ? Math.min(54, 30 + Math.sqrt(count) * 0.9) : 12;
    const classes = ["province-marker", relation];
    if (!count) classes.push("empty");
    if (selected) classes.push("selected");
    return L.divIcon({
      className: "province-marker-host",
      html: `<span class="${classes.join(" ")}" style="--marker-size:${size}px">${count ? markerCount(count) : ""}</span>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
      popupAnchor: [0, -(size / 2 + 7)],
    });
  }

  function popupContent(province, records, ratio) {
    if (!records.length) {
      return `<div class="map-popup"><div class="map-popup-header"><div><span class="map-popup-kicker">İL ÖZETİ</span><strong>${escapeHtml(province.name)}</strong></div><span class="map-popup-count muted">Kayıt yok</span></div></div>`;
    }
    const medianAppraised = median(records.map((record) => record.appraised_value));
    const medianAuction = median(records.map((record) => record.auction_price));
    return `<div class="map-popup"><div class="map-popup-header"><div><span class="map-popup-kicker">İL ÖZETİ</span><strong>${escapeHtml(province.name)}</strong></div><span class="map-popup-count">${integerFormat.format(records.length)} kayıt</span></div><div class="map-popup-values"><div><span>Medyan muhammen</span><strong>${escapeHtml(formatCurrency(medianAppraised, true))}</strong></div><div><span>Medyan ihale</span><strong>${escapeHtml(formatCurrency(medianAuction, true))}</strong></div></div><div class="map-popup-ratio ${relationFor(ratio)}"><span>Gerçekleşme oranı</span><strong>${formatRatio(ratio)}</strong></div></div>`;
  }

  function provinceFilteredRecords(name) {
    return state.filtered.filter((record) => record.province === name);
  }

  function renderMap() {
    elements.mapSelection.textContent = state.selectedProvince === "all" ? "Türkiye geneli" : state.selectedProvince;
    if (!state.map || !state.markerLayer) return;
    state.markerLayer.clearLayers();
    state.markerByProvince.clear();

    state.data.provinces.forEach((province) => {
      const records = provinceFilteredRecords(province.name);
      const ratio = median(records.map((record) => record.ratio));
      const count = records.length;
      const relation = count ? relationFor(ratio) : "empty";
      const marker = L.marker([province.latitude, province.longitude], {
        icon: provinceMarkerIcon(count, relation, state.selectedProvince === province.name),
        keyboard: true,
        title: `${province.name}: ${integerFormat.format(count)} kayıt`,
        riseOnHover: true,
      });
      marker.bindPopup(popupContent(province, records, ratio), {
        className: "province-popup",
        minWidth: 248,
        maxWidth: 290,
        closeButton: false,
        autoPanPadding: [24, 24],
      });
      marker.on("click", () => selectProvince(province.name));
      marker.addTo(state.markerLayer);
      state.markerByProvince.set(province.name, marker);
    });
  }

  function selectProvince(name) {
    state.selectedProvince = name;
    state.page = 1;
    elements.provinceSelect.value = name;
    applyFilters();
    focusSelectedProvince();
  }

  function focusSelectedProvince() {
    if (!state.map || state.selectedProvince === "all") return;
    const province = state.data.provinces.find((item) => item.name === state.selectedProvince);
    if (!province) return;
    const openPopup = () => state.markerByProvince.get(province.name)?.openPopup();
    const target = L.latLng(province.latitude, province.longitude);
    if (state.map.getZoom() >= 7 && state.map.getCenter().distanceTo(target) < 1000) {
      openPopup();
      return;
    }
    state.map.once("moveend", openPopup);
    state.map.flyTo(target, 7, { duration: 0.55 });
  }

  function renderInspector() {
    const title = state.selectedProvince === "all" ? "Türkiye geneli" : state.selectedProvince;
    const records = state.filtered;
    const ratio = median(records.map((record) => record.ratio));
    const below = records.length ? records.filter((record) => record.ratio < 1).length / records.length : null;
    elements.inspectorTitle.textContent = title;
    elements.inspectorCount.textContent = `${integerFormat.format(records.length)} kayıt`;
    elements.inspectorAppraised.textContent = formatCurrency(median(records.map((record) => record.appraised_value)), true);
    elements.inspectorAuction.textContent = formatCurrency(median(records.map((record) => record.auction_price)), true);
    elements.inspectorRatio.textContent = formatRatio(ratio);
    elements.inspectorBelow.textContent = below == null ? "—" : `%${(below * 100).toLocaleString("tr-TR", { maximumFractionDigits: 1 })}`;

    const counts = new Map();
    records.forEach((record) => counts.set(record.property_type, (counts.get(record.property_type) || 0) + 1));
    const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7);
    const max = Math.max(1, ...entries.map(([, count]) => count));
    elements.typeBreakdown.innerHTML = entries.length
      ? entries.map(([type, count]) => `<div class="type-row"><header><span>${escapeHtml(type)}</span><strong>${integerFormat.format(count)}</strong></header><div class="type-track"><span style="width:${(count / max) * 100}%"></span></div></div>`).join("")
      : '<p class="semantic-note">Bu seçimde taşınmaz kaydı yok.</p>';
  }

  const diagonalPlugin = {
    id: "diagonal",
    beforeDatasetsDraw(chart) {
      const { ctx, scales } = chart;
      const minimum = Math.max(scales.x.min, scales.y.min);
      const maximum = Math.min(scales.x.max, scales.y.max);
      if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum >= maximum) return;
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([5, 5]);
      ctx.strokeStyle = "rgba(23, 33, 29, 0.35)";
      ctx.lineWidth = 1;
      ctx.moveTo(scales.x.getPixelForValue(minimum), scales.y.getPixelForValue(minimum));
      ctx.lineTo(scales.x.getPixelForValue(maximum), scales.y.getPixelForValue(maximum));
      ctx.stroke();
      ctx.restore();
    },
  };

  function chartDataset(records, relation, label) {
    return {
      label,
      data: records
        .filter((record) => relationFor(record.ratio) === relation)
        .map((record) => ({ x: record.appraised_value, y: record.auction_price, record })),
      backgroundColor: COLORS[relation],
      borderColor: COLORS[relation],
      pointRadius: 2.2,
      pointHoverRadius: 5,
      pointBorderWidth: 0,
    };
  }

  function renderChart() {
    if (!window.Chart) return;
    const canvas = document.getElementById("priceChart");
    const datasets = [
      chartDataset(state.filtered, "below", "Muhammen altı"),
      chartDataset(state.filtered, "near", "Muhammene yakın"),
      chartDataset(state.filtered, "above", "Muhammen üstü"),
    ];
    if (state.chart) {
      state.chart.data.datasets = datasets;
      state.chart.update("none");
      return;
    }
    state.chart = new Chart(canvas, {
      type: "scatter",
      data: { datasets },
      plugins: [diagonalPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 360 },
        interaction: { mode: "nearest", intersect: false },
        scales: {
          x: {
            type: "logarithmic",
            title: { display: true, text: "Muhammen değer (TL)", color: "#667069", font: { family: "IBM Plex Sans", size: 11 } },
            grid: { color: "rgba(214, 216, 208, 0.65)" },
            ticks: { color: "#667069", callback: (value) => compactCurrency.format(value), maxTicksLimit: 7, font: { family: "IBM Plex Mono", size: 9 } },
          },
          y: {
            type: "logarithmic",
            title: { display: true, text: "İhale bedeli (TL)", color: "#667069", font: { family: "IBM Plex Sans", size: 11 } },
            grid: { color: "rgba(214, 216, 208, 0.65)" },
            ticks: { color: "#667069", callback: (value) => compactCurrency.format(value), maxTicksLimit: 7, font: { family: "IBM Plex Mono", size: 9 } },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            displayColors: false,
            callbacks: {
              title: (items) => items[0]?.raw?.record?.province || "",
              label: (item) => {
                const record = item.raw.record;
                return [
                  `Muhammen: ${formatCurrency(record.appraised_value)}`,
                  `İhale: ${formatCurrency(record.auction_price)}`,
                  `Gerçekleşme: ${formatRatio(record.ratio)}`,
                ];
              },
            },
          },
        },
      },
    });
  }

  function renderDistribution() {
    const bins = [
      { label: "< %60", test: (ratio) => ratio < 0.6 },
      { label: "%60–80", test: (ratio) => ratio >= 0.6 && ratio < 0.8 },
      { label: "%80–95", test: (ratio) => ratio >= 0.8 && ratio < 0.95 },
      { label: "%95–105", test: (ratio) => ratio >= 0.95 && ratio <= 1.05 },
      { label: "%105–125", test: (ratio) => ratio > 1.05 && ratio <= 1.25 },
      { label: "> %125", test: (ratio) => ratio > 1.25 },
    ].map((bin) => ({ ...bin, count: state.filtered.filter((record) => bin.test(record.ratio)).length }));
    const maximum = Math.max(1, ...bins.map((bin) => bin.count));
    elements.distributionBars.innerHTML = bins.map((bin) => `<div class="distribution-row"><span>${bin.label}</span><div class="distribution-track"><span style="width:${(bin.count / maximum) * 100}%"></span></div><strong>${integerFormat.format(bin.count)}</strong></div>`).join("");
  }

  function sortedRecords() {
    const records = [...state.filtered];
    const sorters = {
      ratio_asc: (a, b) => a.ratio - b.ratio,
      ratio_desc: (a, b) => b.ratio - a.ratio,
      appraised_desc: (a, b) => b.appraised_value - a.appraised_value,
      auction_desc: (a, b) => b.auction_price - a.auction_price,
      province_asc: (a, b) => a.province.localeCompare(b.province, "tr") || a.ratio - b.ratio,
    };
    return records.sort(sorters[state.sort]);
  }

  function renderTable() {
    const records = sortedRecords();
    const pageCount = Math.max(1, Math.ceil(records.length / PAGE_SIZE));
    state.page = Math.min(state.page, pageCount);
    const start = (state.page - 1) * PAGE_SIZE;
    const pageRecords = records.slice(start, start + PAGE_SIZE);
    elements.recordsBody.innerHTML = pageRecords.map((record) => {
      const relation = relationFor(record.ratio);
      return `<tr data-province="${escapeHtml(record.province)}"><td><span class="record-ref">${escapeHtml(record.record_ref)}</span></td><td>${escapeHtml(record.province)}</td><td>${escapeHtml(record.property_type)}</td><td class="numeric" title="${escapeHtml(formatCurrency(record.appraised_value))}">${escapeHtml(formatCurrency(record.appraised_value))}</td><td class="numeric" title="${escapeHtml(formatCurrency(record.auction_price))}">${escapeHtml(formatCurrency(record.auction_price))}</td><td class="numeric"><span class="ratio-badge ${relation}" title="${relationLabel(record.ratio)}">${formatRatio(record.ratio)}</span></td></tr>`;
    }).join("");
    elements.recordsBody.querySelectorAll("tr").forEach((row) => {
      row.addEventListener("click", () => selectProvince(row.dataset.province));
    });
    elements.emptyState.hidden = records.length > 0;
    elements.tableCount.textContent = `${integerFormat.format(records.length)} kayıt`;
    elements.pageLabel.textContent = `${state.page} / ${pageCount}`;
    elements.previousPage.disabled = state.page <= 1;
    elements.nextPage.disabled = state.page >= pageCount;
  }

  function csvCell(value) {
    const text = String(value ?? "");
    return `"${text.replaceAll('"', '""')}"`;
  }

  function exportCsv() {
    const header = ["kayit_no", "il", "tasinmaz_turu", "muhammen_deger_tl", "ihale_bedeli_tl", "gerceklesme_orani", "tarih"];
    const lines = state.filtered.map((record) => [
      record.record_ref,
      record.province,
      record.property_type,
      record.appraised_value,
      record.auction_price,
      record.ratio,
      record.transaction_date,
    ].map(csvCell).join(","));
    const blob = new Blob(["\ufeff", header.join(","), "\n", lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "uyap-satis-atlasi.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function initialize() {
    cacheElements();
    if (window.lucide) window.lucide.createIcons();
    try {
      const response = await fetch("/uyap-data", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = await response.json();
      state.records = state.data.records;
      state.filtered = [...state.records];
      elements.headerStatus.textContent = `Canlı SQL görünümü · ${integerFormat.format(state.records.length)} kayıt`;
      if (elements.generatedAt) {
        elements.generatedAt.textContent = `Güncelleme ${new Date(state.data.generated_at).toLocaleString("tr-TR")}`;
      }
      populateControls();
      bindControls();
      initializeMap();
      applyFilters();
      elements.loadingLayer.classList.add("hidden");
    } catch (error) {
      elements.headerStatus.textContent = "Veri yüklenemedi";
      elements.loadingLayer.innerHTML = `<strong>Veri yüklenemedi</strong><span>${escapeHtml(error.message)}</span>`;
    }
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();