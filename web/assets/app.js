(function () {
  "use strict";

  const data = window.__DATA__;
  const EASTERN = "America/New_York";
  const STATUS = { official: "Official", reported: "Reported", estimate: "Estimate", listed: "Listed" };
  const CATEGORIES = [
    ["all", "All"], ["hot", "Hot"], ["preorders", "Preorders"], ["restocks", "Restocks"],
    ["deals", "Deals"], ["releases", "Releases"], ["market", "Market"], ["news", "News"],
  ];
  const PAGE_SIZE = 50;
  const STORAGE_KEY = "sealed-feed-settings";

  const byId = (id) => document.getElementById(id);

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    Object.entries(props || {}).forEach(([key, value]) => {
      if (value === null || value === undefined || value === false) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    children.flat().forEach((child) => {
      if (child === null || child === undefined || child === false) return;
      node.append(child);
    });
    return node;
  }

  // Headlines and links come from outside feeds, so only plain web links are allowed.
  function safeUrl(value) {
    try {
      const parsed = new URL(value);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch (error) {
      return null;
    }
  }

  function externalLink(url, text, className) {
    const href = safeUrl(url);
    if (!href) return document.createTextNode(text);
    return el("a", { href, rel: "noopener noreferrer", target: "_blank", class: className, text });
  }

  const dayFormat = new Intl.DateTimeFormat("en-US", { timeZone: EASTERN, weekday: "short" });
  const clockFormat = new Intl.DateTimeFormat("en-US", { timeZone: EASTERN, hour: "numeric", minute: "2-digit" });
  const dateKeyFormat = new Intl.DateTimeFormat("en-CA", { timeZone: EASTERN, year: "numeric", month: "2-digit", day: "2-digit" });
  const stampFormat = new Intl.DateTimeFormat("en-US", { timeZone: EASTERN, month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  const money = (value) => "$" + Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const signedMoney = (value) => (value >= 0 ? "+" : "−") + money(Math.abs(value));
  const percent = (value) => (value >= 0 ? "+" : "−") + Math.abs(Math.round(value * 100)) + "%";

  function countdown(start, end, now) {
    if (start <= now && now < end) return "Open now";
    const minutes = Math.max(1, Math.round((start - now) / 60000));
    if (minutes < 60) return "in " + minutes + " min";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return "in " + hours + "h " + (minutes % 60) + "m";
    return "in " + Math.floor(hours / 24) + "d " + (hours % 24) + "h";
  }

  function timeAgo(iso) {
    const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
    if (minutes < 2) return "just now";
    if (minutes < 60) return minutes + " min ago";
    const hours = Math.round(minutes / 60);
    if (hours < 24) return hours + "h ago";
    return Math.round(hours / 24) + "d ago";
  }

  function chip(status) {
    return el("span", { class: "chip " + status, text: STATUS[status] || status });
  }

  /* Next up */

  function liveWindows() {
    const now = Date.now();
    return data.windows.filter((window) => Date.parse(window.end) > now);
  }

  function ticket(window, isHero) {
    const start = new Date(window.start);
    const end = new Date(window.end);
    const schedule = window.repeat + ", " + clockFormat.format(start) + " to " + clockFormat.format(end);
    const body = el("div", { class: "ticket-body" },
      el("h3", { text: window.store + ": " + window.title }),
      el("p", { class: "schedule", text: schedule }),
      el("p", { text: window.note }),
      isHero ? el("p", { class: "action", text: window.action }) : null);
    return el("article", { class: "ticket" + (isHero ? " hero" : "") },
      el("div", { class: "stub" },
        el("span", { class: "stub-day", text: dayFormat.format(start) }),
        el("span", { class: "stub-time", text: clockFormat.format(start) })),
      body,
      el("div", { class: "ticket-side" },
        el("span", { class: "countdown", "data-start": window.start, "data-end": window.end, text: countdown(start, end, new Date()) }),
        chip(window.status),
        externalLink(window.link, "Open store", "button-link")));
  }

  function renderNext() {
    const windows = liveWindows();
    const hero = byId("hero");
    const week = byId("week");
    hero.replaceChildren();
    week.replaceChildren();
    if (!windows.length) {
      hero.append(el("p", { class: "empty", text: "No windows scheduled. Check back after the next refresh." }));
      return;
    }
    hero.append(ticket(windows[0], true));
    // Each weekly window appears once, at its next occurrence.
    const seen = new Set([windows[0].id]);
    windows.slice(1).forEach((window) => {
      if (seen.has(window.id)) return;
      seen.add(window.id);
      week.append(ticket(window, false));
    });
  }

  function tickCountdowns() {
    const now = new Date();
    document.querySelectorAll(".countdown").forEach((node) => {
      node.textContent = countdown(new Date(node.dataset.start), new Date(node.dataset.end), now);
    });
  }

  /* Calendar */

  function dateParts(iso) {
    const moment = new Date(iso + "T12:00:00Z");
    return {
      month: moment.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" }),
      day: moment.getUTCDate(),
    };
  }

  function sourceLinks(sources) {
    const nodes = [];
    (sources || []).forEach((source, index) => {
      if (index) nodes.push(" · ");
      nodes.push(externalLink(source.url, source.label));
    });
    return nodes;
  }

  function renderDates() {
    const box = byId("dates");
    box.replaceChildren();
    data.events.forEach((event) => {
      const start = dateParts(event.date);
      const end = event.end ? dateParts(event.end) : null;
      box.append(el("article", { class: "date-row" },
        el("div", { class: "date-block" },
          event.approx ? el("span", { class: "date-range", text: "around" }) : null,
          el("span", { class: "date-month", text: start.month }),
          el("span", { class: "date-day", text: String(start.day) }),
          end ? el("span", { class: "date-range", text: "to " + end.month + " " + end.day }) : null),
        el("div", null,
          el("div", { class: "date-title" },
            el("h3", { text: event.title }),
            chip(event.status),
            event.msrp ? el("span", { class: "chip", text: money(event.msrp) }) : null),
          el("p", { text: event.note || "" }),
          el("p", { class: "small" }, sourceLinks(event.sources)))));
    });

    const undated = byId("undated");
    undated.replaceChildren();
    if (data.undated.length) {
      undated.append(el("h2", { class: "sub-head undated-head", text: "Date not set" }));
      data.undated.forEach((event) => {
        undated.append(el("article", { class: "date-row" },
          el("div", { class: "date-block" }, el("span", { class: "date-month", text: "TBA" })),
          el("div", null,
            el("div", { class: "date-title" }, el("h3", { text: event.title }), chip(event.status)),
            el("p", { text: event.note || "" }),
            el("p", { class: "small" }, sourceLinks(event.sources)))));
      });
    }
    byId("calendar-verified").textContent = "Curated dates checked " + data.verified;
  }

  /* Flip list */

  function defaultSettings() {
    const a = data.assumptions;
    return {
      yourTax: a.your_tax * 100,
      buyerTax: a.buyer_tax * 100,
      store: "none",
      topRated: a.top_rated,
      discount: a.retail_discount * 100,
      sellPct: a.sell_pct * 100,
      supplies: a.supplies,
    };
  }

  function loadSettings() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      return Object.assign(defaultSettings(), saved || {});
    } catch (error) {
      return defaultSettings();
    }
  }

  function saveSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch (error) {
      // Private windows can block storage. The page still works without saving.
    }
  }

  let settings = loadSettings();
  const flipState = { group: "All", profitOnly: false, showOlder: false };

  // Mirrors pipeline/profit.py.
  function estimate(market, buyPrice, shipClass) {
    const a = data.assumptions;
    const hasStore = settings.store === "basic";
    const rate = hasStore ? 0.1235 : a.fee_rate;
    const cap = hasStore ? 2500 : a.fee_cap;
    const sale = market * (settings.sellPct / 100);
    const total = sale * (1 + settings.buyerTax / 100);
    let percentFee = Math.min(total, cap) * rate + Math.max(0, total - cap) * a.over_rate;
    if (settings.topRated) percentFee *= 0.9;
    const perOrder = sale <= a.per_order_cutoff ? a.per_order_small : a.per_order_large;
    const proceeds = sale - percentFee - perOrder - a.shipping[shipClass] - Number(settings.supplies);
    const basis = buyPrice * (1 - settings.discount / 100) * (1 + settings.yourTax / 100);
    const profit = proceeds - basis;
    return { profit, roi: basis ? profit / basis : 0 };
  }

  function buildSettingsForm() {
    const form = byId("settings");
    function number(key, label, step) {
      return el("label", null, label, el("input", {
        type: "number", min: "0", step: step || "0.1", value: String(settings[key]),
        oninput: (event) => {
          settings[key] = Number(event.target.value) || 0;
          saveSettings();
          renderFlip();
          renderDrawings();
        },
      }));
    }
    const storeSelect = el("select", {
      onchange: (event) => {
        settings.store = event.target.value;
        saveSettings();
        renderFlip();
        renderDrawings();
      },
    },
      el("option", { value: "none", text: "No Store or Starter (13.25%)" }),
      el("option", { value: "basic", text: "Basic Store or higher (12.35%)" }));
    storeSelect.value = settings.store;
    const topRated = el("input", {
      type: "checkbox",
      onchange: (event) => {
        settings.topRated = event.target.checked;
        saveSettings();
        renderFlip();
        renderDrawings();
      },
    });
    topRated.checked = Boolean(settings.topRated);
    form.append(
      number("yourTax", "Your sales tax (%)"),
      number("buyerTax", "Buyer's sales tax (%)"),
      el("label", null, "eBay plan", storeSelect),
      number("discount", "Store discount (%)"),
      number("sellPct", "Sell at % of market", "1"),
      number("supplies", "Packing supplies ($)", "0.25"),
      el("label", { class: "check" }, topRated, "Top Rated Plus (10% off fee)"));
  }

  function currentProducts() {
    return data.market.products.filter((product) => flipState.showOlder || product.current);
  }

  function renderFlipGroups() {
    const box = byId("flip-groups");
    box.replaceChildren();
    const groups = ["All", ...new Set(data.market.products.map((product) => product.group))];
    groups.forEach((group) => {
      box.append(el("button", {
        class: "pill", type: "button", "aria-pressed": String(flipState.group === group), text: group,
        onclick: () => {
          flipState.group = group;
          renderFlipGroups();
          renderFlip();
        },
      }));
    });
  }

  function renderFlip() {
    const body = byId("flip-body");
    body.replaceChildren();
    const rows = currentProducts()
      .filter((product) => flipState.group === "All" || product.group === flipState.group)
      .map((product) => ({ product, result: estimate(product.market, product.msrp, product.ship) }))
      .filter((row) => !flipState.profitOnly || row.result.profit > 0)
      .sort((a, b) => b.result.profit - a.result.profit);

    if (!rows.length) {
      body.append(el("tr", null, el("td", { colspan: "7", class: "empty", text: "Nothing matches these filters." })));
    }
    rows.forEach(({ product, result }) => {
      const tone = result.profit >= 0 ? "gain" : "loss";
      const meta = [product.type, product.release ? "Out " + product.release : ""].filter(Boolean).join(" · ");
      body.append(el("tr", null,
        el("td", { class: "cell-name" },
          el("span", { class: "product-name", text: product.name }),
          el("span", { class: "product-meta" }, meta, product.presale ? " " : "", product.presale ? el("span", { class: "chip presale", text: "Presale" }) : null)),
        el("td", { class: "num", "data-label": "Retail", text: money(product.msrp) }),
        el("td", { class: "num", "data-label": "Market", text: money(product.market) }),
        el("td", { class: "num", "data-label": "eBay asks", text: product.ebay_ask ? money(product.ebay_ask) : "–" }),
        el("td", { class: "num " + tone, "data-label": "Profit", text: signedMoney(result.profit) }),
        el("td", { class: "num " + tone, "data-label": "Return", text: percent(result.roi) }),
        el("td", { class: "cell-check" },
          el("div", { class: "check-links" },
            externalLink(product.sold_url, "eBay sold"),
            externalLink(product.url, "TCGplayer")))));
    });

    const updated = "Prices from TCGplayer, updated " + timeAgo(data.market.generated_at) + ".";
    const asks = data.market.ebay_used
      ? " eBay asks are the middle of the five cheapest current listings, not sold prices."
      : " eBay asks will appear once eBay keys are added.";
    byId("flip-note").textContent = updated + asks + " Retail assumes MSRP. Older sets are rarely sold at MSRP anymore. Check eBay sold listings before you buy.";
  }

  /* Drawings */

  function renderDrawings() {
    const box = byId("drawing-list");
    box.replaceChildren();
    data.drawings.forEach((drawing) => {
      const rows = drawing.items.map((item) => {
        if (item.market === null) {
          return el("tr", null,
            el("td", { class: "cell-name" }, el("span", { class: "product-name", text: item.name })),
            el("td", { class: "num", "data-label": "Drawing", text: money(item.price) }),
            el("td", { class: "num", "data-label": "Market", text: "–" }),
            el("td", { class: "num", "data-label": "Profit", text: "–" }),
            el("td", { class: "cell-check" }, "No price data"));
        }
        const result = estimate(item.market, item.price, item.ship);
        const verdict = result.profit >= 15 ? ["yes", "Worth entering"] : result.profit >= 0 ? ["thin", "Thin"] : ["no", "Skip"];
        return el("tr", null,
          el("td", { class: "cell-name" }, el("span", { class: "product-name", text: item.name })),
          el("td", { class: "num", "data-label": "Drawing", text: money(item.price) }),
          el("td", { class: "num", "data-label": "Market", text: money(item.market) }),
          el("td", { class: "num " + (result.profit >= 0 ? "gain" : "loss"), "data-label": "Profit", text: signedMoney(result.profit) }),
          el("td", { class: "cell-check" }, el("span", { class: "verdict " + verdict[0], text: verdict[1] })));
      });
      box.append(el("article", { class: "drawing" },
        el("div", { class: "drawing-head" },
          el("h3", { text: drawing.title }),
          el("span", { class: "muted small" }, "Prices from ", externalLink(drawing.source.url, drawing.source.label))),
        el("div", { class: "table-wrap" },
          el("table", { class: "flip" },
            el("thead", null, el("tr", null,
              el("th", { scope: "col", text: "Product" }),
              el("th", { scope: "col", class: "num", text: "Drawing" }),
              el("th", { scope: "col", class: "num", text: "Market" }),
              el("th", { scope: "col", class: "num", text: "Profit" }),
              el("th", { scope: "col", text: "Verdict" }))),
            el("tbody", null, rows)))));
    });
  }

  /* Feed */

  const feedState = { tab: "all", shown: PAGE_SIZE };

  function matchesTab(item, tab) {
    if (tab === "all") return true;
    if (tab === "hot") return item.hot;
    return item.category === tab;
  }

  function renderFeedTabs() {
    const box = byId("feed-tabs");
    box.replaceChildren();
    CATEGORIES.forEach(([key, label]) => {
      const count = data.items.filter((item) => matchesTab(item, key)).length;
      box.append(el("button", {
        class: "pill", type: "button", "aria-pressed": String(feedState.tab === key),
        onclick: () => {
          feedState.tab = key;
          feedState.shown = PAGE_SIZE;
          renderFeedTabs();
          renderFeed();
        },
      }, label, el("span", { class: "count", text: String(count) })));
    });
  }

  function renderFeed() {
    const list = byId("feed-list");
    list.replaceChildren();
    const items = data.items.filter((item) => matchesTab(item, feedState.tab));
    if (!items.length) list.append(el("li", { class: "empty", text: "Nothing here yet." }));
    items.slice(0, feedState.shown).forEach((item) => {
      const source = item.publisher || item.source;
      const label = CATEGORIES.find(([key]) => key === item.category);
      list.append(el("li", null,
        externalLink(item.link, item.title),
        el("div", { class: "feed-meta" },
          item.hot ? el("span", { class: "chip hot", text: "Hot" }) : null,
          feedState.tab === "all" && label ? el("span", { class: "chip", text: label[1] }) : null,
          el("span", { text: source }),
          item.stores.length ? el("span", { text: "· " + item.stores.join(", ") }) : null,
          el("span", { text: "· " + timeAgo(item.published) }))));
    });
    const more = byId("feed-more");
    more.hidden = items.length <= feedState.shown;
  }

  /* Subscribe */

  function copyButton(text) {
    return el("button", {
      class: "copy", type: "button", text: "Copy",
      onclick: (event) => {
        const button = event.currentTarget;
        navigator.clipboard.writeText(text).then(
          () => { button.textContent = "Copied"; },
          () => { button.textContent = "Press and hold the link"; });
      },
    });
  }

  function linkRow(label, path) {
    const url = data.site_url + path;
    return el("li", null, el("a", { href: path, text: label }), copyButton(url));
  }

  function renderSubscribe() {
    byId("rss-list").replaceChildren(...data.feeds.map((feed) => linkRow(feed.label, feed.path)));
    const calendarAddress = (data.site_url + "calendar.ics").replace(/^https:/, "webcal:");
    const googleLink = "https://calendar.google.com/calendar/r?cid=" + encodeURIComponent(calendarAddress);
    byId("calendar-link").replaceChildren(
      el("li", null, externalLink(googleLink, "Add to Google Calendar")),
      linkRow("Link for other calendar apps", "calendar.ics"));
    const working = data.sources.filter((source) => source.ok).length;
    byId("source-summary").textContent = working + " of " + data.sources.length + " sources responded on the last check.";
    byId("source-list").replaceChildren(...data.sources.map((source) =>
      el("li", { class: source.ok ? "" : "source-bad" },
        el("span", { text: source.name }),
        el("span", { text: source.ok ? source.count + " items" : "Unavailable" }))));
  }

  function renderFreshness() {
    byId("freshness").textContent = "Feed checked " + stampFormat.format(new Date(data.generated_at)) + " ET";
  }

  byId("profit-only").addEventListener("change", (event) => {
    flipState.profitOnly = event.target.checked;
    renderFlip();
  });
  byId("show-older").addEventListener("change", (event) => {
    flipState.showOlder = event.target.checked;
    renderFlip();
  });
  byId("feed-more").addEventListener("click", () => {
    feedState.shown += PAGE_SIZE;
    renderFeed();
  });

  renderFreshness();
  renderNext();
  renderDates();
  buildSettingsForm();
  renderFlipGroups();
  renderFlip();
  renderDrawings();
  renderFeedTabs();
  renderFeed();
  renderSubscribe();
  setInterval(() => {
    tickCountdowns();
    if (Date.parse(data.windows[0] && data.windows[0].end) < Date.now()) renderNext();
  }, 30000);
})();
